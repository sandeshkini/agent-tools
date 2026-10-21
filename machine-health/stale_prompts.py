"""stale-prompts (a machine-health check): close macOS permission prompts that were left open for over an hour
for an app that already has the permission.

The prompt for Accessibility / Screen Recording lives in a `universalAccessAuthWarn` process. When Sandesh grants
the permission in System Settings instead of in the prompt, the dialog just stays (a "cos Node wants Screen
Recording" prompt sat there 7 h and stuck-watch re-alerted all day). We close it by ending that process, NEVER by
pressing Deny (Deny can record a refusal in TCC).

An app "already has the permission" when tccd logged an allowed result (authValue=2) for the same app and service
after the prompt appeared. If the log doesn't show that, the prompt is left alone and reported.
"""
import json, os, re, signal, subprocess, time

import stuck_watch as sw

PROC_NAME = "universalAccessAuthWarn"
MIN_AGE_S = lambda: int(sw.conf("MACHINE_HEALTH_STALE_PROMPT_S", 3600))


def etime_s(t):
    d = 0
    if "-" in t:
        d, t = t.split("-")
    p = [int(x) for x in t.split(":")]
    while len(p) < 3:
        p.insert(0, 0)
    return int(d) * 86400 + p[0] * 3600 + p[1] * 60 + p[2]


def prompt_processes():
    """[(pid, age_seconds)] of open universalAccessAuthWarn processes."""
    out = subprocess.run(["ps", "-axo", "pid=,etime=,comm="], capture_output=True, text=True).stdout
    res = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and os.path.basename(parts[2]) == PROC_NAME:
            res.append((int(parts[0]), etime_s(parts[1])))
    return res


def parse_events(lines):
    """From tccd ndjson log lines: (prompts, grants).
    prompts = [{t, who, service}]; grants = [{t, ids:set, service}] for allowed results."""
    ctx, prompts, grants = {}, [], []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("eventMessage", "")
        m = sw.RX_MSGID.search(msg)
        if not m:
            continue
        key = (e.get("processID"), m.group(1))
        ts = sw._ts(e.get("timestamp")) or time.time()
        svc = re.search(r"service=(\w+)", msg)
        if msg.startswith("AUTHREQ_CTX"):
            ctx.setdefault(key, {"ids": set()})["service"] = svc and svc.group(1)
        elif msg.startswith("AUTHREQ_ATTRIBUTION"):
            c = ctx.setdefault(key, {"ids": set()})
            c["ids"] |= set(re.findall(r"identifier=([^,}\s]+)", msg))
        elif msg.startswith("AUTHREQ_PROMPTING"):
            ident = re.search(r"Resp:\{TCCDProcess: identifier=([^,]+)", msg)
            c = ctx.get(key, {})
            prompts.append({"t": ts, "service": (svc and svc.group(1)) or c.get("service"),
                            "who": ident.group(1) if ident else None,
                            "ids": set(c.get("ids", ())) | ({ident.group(1)} if ident else set())})
        elif msg.startswith("AUTHREQ_RESULT"):
            c = ctx.pop(key, {})
            val = re.search(r"authValue=(\d+)", msg)
            if val and val.group(1) == "2" and c.get("service"):
                grants.append({"t": ts, "ids": c.get("ids", set()), "service": c["service"]})
    return prompts, grants


def decide(started_at, prompts, grants, window=180):
    """Which prompt opened this dialog, and does that app have the permission now?
    Returns (verdict, detail): verdict is 'stale' | 'unknown'."""
    near = [p for p in prompts if abs(p["t"] - started_at) <= window and p["who"]]
    if not near:
        return "unknown", "no matching prompt in the TCC log"
    p = min(near, key=lambda p: abs(p["t"] - started_at))
    for g in grants:
        if g["t"] > p["t"] and g["service"] == p["service"] and (g["ids"] & p["ids"]):
            return "stale", f"{p['who']} wants {sw.service_name(p['service'])}, already granted"
    return "unknown", f"{p['who']} wants {sw.service_name(p['service'])}; no grant seen in the log"


def tcc_log(hours):
    out = subprocess.run(["/usr/bin/log", "show", "--last", f"{max(hours, 1)}h", "--style", "ndjson",
                          "--predicate", sw.TCC_PREDICATE], capture_output=True, text=True, timeout=240).stdout
    return out.splitlines()


def run(dry=False, force=False):
    procs = prompt_processes()
    if not procs:
        return "no permission prompts open"
    old = [(pid, age) for pid, age in procs if age >= (0 if force else MIN_AGE_S())]
    if not old:
        return f"{len(procs)} prompt(s) open, none older than {MIN_AGE_S() // 60} min"
    hours = int(max(a for _, a in old) / 3600) + 2
    try:
        prompts, grants = parse_events(tcc_log(hours))
    except Exception as e:
        return f"TCC log unreadable ({type(e).__name__}); left alone"
    notes = []
    for pid, age in old:
        verdict, detail = decide(time.time() - age, prompts, grants)
        if verdict == "stale":
            sw.log("PROMPT", f"{'[dry] ' if dry else ''}closing stale prompt (pid {pid}, open {age // 60} min): {detail}")
            if not dry:
                try:
                    os.kill(pid, signal.SIGTERM)    # kill the prompt process; never click Deny
                except OSError:
                    pass
            notes.append(f"closed pid {pid}")
        else:
            notes.append(f"pid {pid} kept ({detail})")
    return "; ".join(notes)
