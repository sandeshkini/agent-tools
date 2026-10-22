#!/usr/bin/env python3
"""mem-guard (a machine-health check): stop aibo-mac from showing "Your system has run out of application memory".

Every 60 s it looks at memory. Only when pressure is warning/critical, or swap is over 70% of its size, it clears
(and logs each action), in this order:
  1. agent-browser daemons that are idle (no command for 10 min) and big (footprint over 1 GB; a fresh one is 12 MB,
     the leaked ones on 2026-10-21 were 1.4 GB each, 91 of them)
  2. orphan headless Chromium/WebKit from Playwright (path has ms-playwright, parent is launchd, older than 5 min)
  3. booted iOS Simulators no agent uses (no build/test running, not the heavy-job lock's holder): simctl shutdown <udid>
  4. throwaway cos dev hubs / fake nodes (scripts/dev_hub.py, fake_node.py) older than 30 min
One ntfy per incident (a run that freed something, at most every 15 min) says what was freed.

It matches only the four kinds above by name/path. It never touches Sandesh's own Chrome, his apps, the cos hub
(docker/colima), cptr or a running claude; the process holding the heavy-job lock (and what it started) is spared.
The lock itself is cos scripts/heavy-lock.sh (/tmp/cos-heavy.lock), taken by test.sh e2e and ios/uitest.sh.
Config (machine-health config file or env): MEM_GUARD_SWAP_PCT=70 MEM_GUARD_DAEMON_GB=1 MEM_GUARD_DAEMON_IDLE_S=600
MEM_GUARD_HUB_AGE_S=1800 MEM_GUARD_ORPHAN_AGE_S=300 MEM_GUARD_ALERT_EVERY_S=900
"""
import json, os, re, signal, subprocess, time

import stuck_watch as sw
from stale_prompts import etime_s
import mem_reaper as mr

LOCK_DIR = os.environ.get("COS_HEAVY_LOCK", "/tmp/cos-heavy.lock")
STATE = os.path.join(os.path.expanduser("~"), ".local", "state", "machine-health", "mem-guard.json")
PROTECT = re.compile(r"/claude( |$)|\bclaude\b.*--output-format|cptr|colima|limactl|com\.docker|/Applications/(?!Xcode)", re.I)
HUB = re.compile(r"scripts/(dev_hub|fake_node)\.py|(^|/| )(dev_hub|fake_node)\.py")
SIM_ACTIVE = re.compile(r"xcodebuild|XCBBuildService|xctest|swift-build|simctl|xcrun", re.I)
UDID = re.compile(r"Devices/([0-9A-F]{8}-(?:[0-9A-F]{4}-){3}[0-9A-F]{12})", re.I)


def c(key, default):
    v = os.environ.get(key)
    return type(default)(v) if v else sw.conf(key, default)


def procs():
    """-> {pid: dict(ppid, age, rss_kb, args)}"""
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,etime=,rss=,args="], capture_output=True, text=True).stdout
    d = {}
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(\d+)\s+(.*)", line)
        if m:
            d[int(m.group(1))] = dict(ppid=int(m.group(2)), age=etime_s(m.group(3)), rss=int(m.group(4)), args=m.group(5))
    return d


def under(pid, root, P):
    """is pid root or one of its descendants?"""
    for _ in range(40):
        if pid == root:
            return True
        pid = P.get(pid, {}).get("ppid", 0)
        if pid <= 1:
            return False
    return False


def lock_holder():
    try:
        return int(open(os.path.join(LOCK_DIR, "pid")).read().strip())
    except (OSError, ValueError):
        return 0


def memory():
    """-> (swap_used_gb, swap_total_gb, pressure level 1/2/4)"""
    m = re.search(r"total = ([\d.]+)M\s+used = ([\d.]+)M", mr.sh("sysctl", "-n", "vm.swapusage"))
    tot, used = (float(m.group(1)) / 1024, float(m.group(2)) / 1024) if m else (0.0, 0.0)
    return used, tot, mr.pressure_level()


def triggered(used, tot, level):
    pct = 100 * used / tot if tot else 0
    return level >= 2 or pct > c("MEM_GUARD_SWAP_PCT", 70.0), pct


def footprint_gb(pid):
    fm = re.search(r"Footprint:\s*([\d.]+)\s*(KB|MB|GB)", mr.sh("footprint", str(pid), t=30))
    return float(fm.group(1)) / {"KB": 1048576, "MB": 1024, "GB": 1}[fm.group(2)] if fm else 0.0


def term(pid, why, dry, done):
    sw.log("MEMGUARD", f"{'[dry] ' if dry else ''}kill {pid}: {why}")
    done.append(why)
    if not dry:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def clear(P, dry=False):
    """The four steps. -> list of one-line descriptions of what was freed."""
    done, holder, me = [], lock_holder(), os.getpid()
    spared = lambda pid: holder and under(pid, holder, P)
    # 1. idle, big agent-browser daemons
    refs = mr.session_files()
    now, big, idle_s = time.time(), c("MEM_GUARD_DAEMON_GB", 1.0), c("MEM_GUARD_DAEMON_IDLE_S", 600)
    for pid, p in P.items():
        if not p["args"].split(" ")[0].endswith("/agent-browser-darwin-arm64") or p["age"] < c("MEM_GUARD_DAEMON_MIN_AGE_S", 120):
            continue
        sess = refs.get(pid)
        idle = now - mr.last_active(sess) if sess else 1e9
        gb = footprint_gb(pid) if idle > idle_s else 0.0
        if gb > big and idle > idle_s:
            term(pid, f"agent-browser daemon {('session ' + sess + ', idle ' + format(idle / 60, '.0f') + ' min') if sess else 'unreachable (no session file)'}, {gb:.1f} GB", dry, done)
    # 2. orphan Playwright browsers
    for pid, p in P.items():
        a = p["args"]
        if "ms-playwright" in a and p["ppid"] == 1 and p["age"] > c("MEM_GUARD_ORPHAN_AGE_S", 300) and not PROTECT.search(a) and "--type=" not in a:
            term(pid, f"orphan Playwright browser {os.path.basename(a.split(' ')[0])} ({p['rss'] // 1024} MB, parent gone)", dry, done)
    # (their renderers/GPU helpers die with the main process; any left with ppid 1 are caught next tick)
    for pid, p in P.items():
        if "ms-playwright" in p["args"] and "--type=" in p["args"] and p["ppid"] == 1 and p["age"] > 300:
            term(pid, f"orphan Playwright helper ({p['rss'] // 1024} MB)", dry, done)
    # 3. booted simulators nobody uses
    sims = {}
    for pid, p in P.items():
        if "launchd_sim" in p["args"]:
            u = UDID.search(p["args"])
            if u and not spared(pid):
                sims[u.group(1).upper()] = max(sims.get(u.group(1).upper(), 0), p["age"])
    busy = [p["args"][:80] for pid, p in P.items() if pid != me and "machine_health" not in p["args"] and SIM_ACTIVE.search(p["args"].split(" -")[0][:200])]
    if sims and not busy and not holder:
        for u, age in sims.items():
            if age > c("MEM_GUARD_SIM_AGE_S", 600):
                sw.log("MEMGUARD", f"{'[dry] ' if dry else ''}xcrun simctl shutdown {u}: simulator booted {age // 60} min, no build/test running")
                done.append(f"iOS Simulator {u[:8]} (up {age // 60} min)")
                if not dry:
                    subprocess.run(["xcrun", "simctl", "shutdown", u], capture_output=True, timeout=60)
    # 4. leftover dev hubs / fake nodes
    for pid, p in P.items():
        if HUB.search(p["args"]) and "python" in p["args"].lower() and pid != me and p["age"] > c("MEM_GUARD_HUB_AGE_S", 1800) \
                and not spared(pid) and not PROTECT.search(p["args"].split("python")[0]):
            term(pid, f"leftover dev hub/fake node ({p['args'][:70]}) up {p['age'] // 60} min", dry, done)
    return done


def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def run(dry=False, force=False):
    used, tot, level = memory()
    hot, pct = triggered(used, tot, level)
    head = f"swap {used:.1f}/{tot:.1f} GB ({pct:.0f}%), pressure {level}"
    if not (hot or force):
        return head + "; fine"
    done = clear(procs(), dry)
    if not done:
        return head + "; hot, nothing to free"
    st, now = load(), time.time()
    if not dry and now - st.get("last_alert", 0) > c("MEM_GUARD_ALERT_EVERY_S", 900):
        sw.notify(f"aibo-mac memory guard ({head}) freed: " + "; ".join(done[:8]) + (f"; +{len(done) - 8} more" if len(done) > 8 else ""), 3)
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump({"last_alert": now}, open(STATE, "w"))
    return f"{head}; freed {len(done)}: {done[0]}" + (" ..." if len(done) > 1 else "")


if __name__ == "__main__":
    import sys
    print(run(dry="--dry-run" in sys.argv, force="--force" in sys.argv))
