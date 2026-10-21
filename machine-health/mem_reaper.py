#!/usr/bin/env python3
"""mem-reaper (a machine-health check): keep aibo-mac from running out of memory because of abandoned
agent-browser daemons and Agent Chrome tabs/renderers. run() is called every 2 min by machine-health; it does the
full clean-up every 30 min, or at once when swap > 20 GB or memory pressure is critical (and then sends one ntfy
push with the top 5 processes). Thresholds: MEM_REAPER_* env vars or the machine-health config file.
"""
import json, os, re, signal, subprocess, sys, time, urllib.request
from datetime import datetime

HOME = os.path.expanduser("~")
AB_DIR = os.path.join(HOME, ".agent-browser")
STATE = os.path.join(HOME, ".local", "state", "machine-health", "mem-reaper.json")
LOG = os.path.join(HOME, "Library", "Logs", "machine-health.log")
DRY = False        # set by run(dry=True)


def e(k, d=None):
    import stuck_watch      # shares the machine-health config file with stuck-watch
    return os.environ.get(k) or stuck_watch.CFG.get(k) or d



CFG = {
    "cdp": e("MEM_REAPER_CDP", "http://127.0.0.1:9333"),
    "chrome_dir": e("MEM_REAPER_CHROME_DIR", "Chrome-Agent"),
    "daemon_idle_s": int(e("MEM_REAPER_DAEMON_IDLE_S", 2 * 3600)),
    "orphan_min_age_s": int(e("MEM_REAPER_ORPHAN_AGE_S", 900)),
    "tab_age_s": int(e("MEM_REAPER_TAB_AGE_S", 3 * 3600)),
    "renderer_gb": float(e("MEM_REAPER_RENDERER_GB", 3)),
    "swap_alert_gb": float(e("MEM_REAPER_SWAP_GB", 20)),
    "interval_s": int(e("MEM_REAPER_INTERVAL_S", 1800)),
    "alert_every_s": int(e("MEM_REAPER_ALERT_EVERY_S", 3 * 3600)),
}
KEEP_TAB = re.compile(r"login|log-in|signin|sign-in|signon|sign_in|/auth|oauth|sso|accounts\.|1password|passkey|"
                      r"mfa|2fa|otp|verify|challenge|^about:|^chrome|^devtools|^edge", re.I)


def log(msg):
    line = f"{datetime.now().isoformat(timespec='seconds')} MEM {'[dry] ' if DRY else ''}{msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def sh(*cmd, t=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=t).stdout
    except Exception:
        return ""


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(s, open(STATE, "w"))


def etime_s(t):
    d = 0
    if "-" in t:
        d, t = t.split("-")
    p = [int(x) for x in t.split(":")]
    while len(p) < 3:
        p.insert(0, 0)
    return int(d) * 86400 + p[0] * 3600 + p[1] * 60 + p[2]


def swap_used_gb():
    m = re.search(r"used = ([\d.]+)M", sh("sysctl", "-n", "vm.swapusage"))
    return float(m.group(1)) / 1024 if m else 0.0


def pressure_level():
    try:
        return int(sh("sysctl", "-n", "kern.memorystatus_vm_pressure_level").strip())   # 1 normal 2 warn 4 critical
    except ValueError:
        return 1


def kill(pid, why):
    log(f"kill {pid}: {why}")
    if not DRY:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


# ------------------------------------------------------------------ agent-browser daemons
def session_files():
    """pid -> session name, for daemons a CLI can still reach (their pid file is current)."""
    out = {}
    try:
        names = os.listdir(AB_DIR)
    except OSError:
        return out
    for f in names:
        if f.endswith(".pid"):
            try:
                out[int(open(os.path.join(AB_DIR, f)).read().strip())] = f[:-4]
            except (OSError, ValueError):
                pass
    return out


def last_active(session):
    """Latest write to the session's files; .target is rewritten whenever a command picks a tab."""
    m = 0
    for ext in ("target", "config", "pid", "sock"):
        try:
            m = max(m, os.path.getmtime(os.path.join(AB_DIR, f"{session}.{ext}")))
        except OSError:
            pass
    return m


def reap_daemons():
    refs, now, n = session_files(), time.time(), 0
    for line in sh("ps", "-axo", "pid=,ppid=,etime=,rss=,args=").splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s+(\S+)", line)
        if not m or not m.group(5).endswith("/agent-browser-darwin-arm64"):
            continue
        pid, age = int(m.group(1)), etime_s(m.group(3))
        if age < CFG["orphan_min_age_s"]:
            continue
        sess = refs.get(pid)
        if sess is None:
            # no session file points at it any more: no CLI can reach it again, so nothing is using it
            kill(pid, f"agent-browser daemon unreachable (no session file), age {age // 3600}h, rss {int(m.group(4)) // 1024} MB")
            n += 1
            continue
        idle = now - last_active(sess)
        if age > CFG["daemon_idle_s"] and idle > CFG["daemon_idle_s"]:
            kill(pid, f"agent-browser daemon session '{sess}' idle {idle / 3600:.1f}h, age {age / 3600:.1f}h")
            n += 1
    return n


# ------------------------------------------------------------------ Agent Chrome
def routines_running():
    """A personal-agent routine is a running `pa-routine` process. Looked up with ps, not by reading
    personal-agent/var (~/Documents is privacy-protected: the launchd app would hang on a prompt)."""
    return "bin/pa-routine" in sh("ps", "-axo", "args=")


def cdp(path, t=8):
    return urllib.request.urlopen(CFG["cdp"] + path, timeout=t).read().decode()


def reap_tabs(state, close=True):
    try:
        tabs = json.loads(cdp("/json/list"))
    except Exception as ex:
        log(f"Agent Chrome /json not answering ({type(ex).__name__}); tabs skipped")
        return 0
    seen, now, n = state.setdefault("tabs", {}), time.time(), 0
    running = routines_running()
    live = set()
    for t in tabs:
        if t.get("type") != "page":
            continue
        tid, url = t["id"], t.get("url", "")
        live.add(tid)
        sig = f"{url}|{t.get('title', '')}"
        rec = seen.get(tid)
        if not rec or rec["sig"] != sig:        # new tab, or the page moved on: restart its clock
            seen[tid] = {"sig": sig, "since": now}
            continue
        age = now - rec["since"]
        if not close or age <= CFG["tab_age_s"] or KEEP_TAB.search(url) or running:
            continue
        log(f"close tab {tid} ({url[:80]}): unchanged {age / 3600:.1f}h")
        if not DRY:
            try:
                cdp(f"/json/close/{tid}")
            except Exception as ex:
                log(f"  close failed: {ex}")
        n += 1
    for tid in list(seen):
        if tid not in live:
            del seen[tid]
    return n


def reap_renderers():
    n = 0
    for line in sh("ps", "-axo", "pid=,args=").splitlines():
        m = re.match(r"\s*(\d+)\s+(.*)", line)
        if not m or "--type=renderer" not in m.group(2) or CFG["chrome_dir"] not in m.group(2):
            continue
        pid = int(m.group(1))
        fm = re.search(r"Footprint:\s*([\d.]+)\s*(KB|MB|GB)", sh("footprint", str(pid), t=30))   # counts swapped pages too
        if not fm:
            continue
        gb = float(fm.group(1)) / {"KB": 1048576, "MB": 1024, "GB": 1}[fm.group(2)]
        if gb > CFG["renderer_gb"]:
            kill(pid, f"Agent Chrome renderer footprint {gb:.1f} GB")
            n += 1
    return n


# ------------------------------------------------------------------ alert
def ntfy_config():
    import stuck_watch
    return stuck_watch.ntfy_config()


def top5():
    out = sh("top", "-l", "1", "-o", "mem", "-n", "5", "-stats", "pid,mem,command", t=40)
    rows = [l.split(None, 2) for l in out.splitlines() if re.match(r"\s*\d+\s+\d", l)]
    return [f"{r[2].strip()} {r[1]}" for r in rows[:5] if len(r) == 3]


def alert(swap, level, acted):
    msg = (f"aibo-mac memory: swap {swap:.1f} GB, pressure {'critical' if level >= 4 else level}. "
           f"Reaper ran: {acted}. Top 5: " + "; ".join(top5()))
    log("ALERT " + msg)
    url, topic, tok = ntfy_config()
    if DRY or not url:
        return
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    body = json.dumps({"topic": topic, "title": "aibo-mac low on memory", "message": msg, "priority": 4,
                       "tags": ["warning"]}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url.rstrip("/") + "/", data=body, method="POST", headers=h), timeout=10).read()
    except Exception as ex:
        log(f"ntfy failed: {ex}")


def run(force=False, dry=False):
    """One tick. Returns a one-line summary for `machine-health status`."""
    global DRY
    DRY = dry
    st, now = load_state(), time.time()
    swap, level = swap_used_gb(), pressure_level()
    hot = swap > CFG["swap_alert_gb"] or level >= 4
    due = now - st.get("last_run", 0) >= CFG["interval_s"]
    # tabs are tracked every tick so their age clock is accurate; closing/killing only on a full run
    if not (force or hot or due):
        reap_tabs(st, close=False)
        if not dry:
            save_state(st)
        return f"swap {swap:.1f} GB, pressure {level}; watching"
    d = reap_daemons()
    t = reap_tabs(st)
    r = reap_renderers()
    acted = f"{d} daemons, {t} tabs, {r} renderers"
    log(f"run ({'swap/pressure' if hot else 'timer' if due else 'forced'}): swap {swap:.1f} GB, pressure {level}; {acted}")
    if not dry:
        st["last_run"] = now
        save_state(st)
    if hot and (now - st.get("last_alert", 0) > CFG["alert_every_s"]):
        if not dry:
            st["last_alert"] = now
            save_state(st)
        alert(swap, level, acted)
    return f"swap {swap:.1f} GB, pressure {level}; reaped {acted}"


if __name__ == "__main__":
    print(run(force="--force" in sys.argv, dry="--dry-run" in sys.argv))
