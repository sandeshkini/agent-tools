#!/usr/bin/env python3
"""machine-health: one watcher for what goes wrong on a machine that agents work on.

  machine-health status                 each check: running? last run, last result, plus the job and the log
  machine-health run                    the daemon (what the LaunchAgent runs): every check on its own schedule
  machine-health run --once [--dry-run] [--check NAME]
                                        one pass of every check (or one) now; --dry-run changes nothing, sends nothing
  machine-health install [--check|--uninstall]
                                        install the single LaunchAgent, migrating and removing the old ones

Checks (see README.md):
  stuck-watch     agent commands stuck on a macOS prompt / unanswered dialogs; ntfy alerts      (macOS, every 60 s)
  mem-reaper      abandoned agent-browser daemons, stale Agent Chrome tabs, huge renderers, swap watch  (macOS, 2 min)
  stale-prompts   permission prompts left open > 1 h for an app that already has the permission (macOS, 5 min)
  idle-simulators iOS Simulators booted over 2 h with no build/test running: shut down + one alert (macOS, 10 min)
  docker-hygiene  report-only list of old/dangling images; deletes nothing                       (daily)

One config file ~/.config/machine-health/config (KEY=VALUE; STUCK_WATCH_*, MEM_REAPER_*, MACHINE_HEALTH_*, NTFY_*),
one log ~/Library/Logs/machine-health.log, one LaunchAgent.
"""
import json, os, shutil, subprocess, sys, threading, time
from datetime import datetime

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".local", "state", "machine-health")
STATUS = os.path.join(STATE_DIR, "status.json")
LOG = os.path.join(HOME, "Library", "Logs", "machine-health.log")
CONFIG = os.path.join(HOME, ".config", "machine-health", "config")
LABEL = "com.sandesh.machine-health"
PA_LABEL = "com.sandesh.pa.machine-health"
APP_NAME = "Machine Health"
OLD_LABELS = ["com.sandesh.stuck-watch", "com.sandesh.pa.stuck-watch", "com.sandesh.mem-reaper"]
DARWIN = sys.platform == "darwin"
# launchd gives a bare PATH; docker, uv, brew tools live here
os.environ["PATH"] = os.environ.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin:" + os.path.join(HOME, ".local/bin")


def checks():
    """name -> {interval, darwin_only, make() -> callable(dry, force) -> summary str, what}."""
    import stuck_watch, mem_reaper, stale_prompts, docker_hygiene, idle_simulators
    sw = {}

    def stuck(dry=False, force=False):
        if "w" not in sw:
            sw["w"] = stuck_watch.StuckWatch(once=force)
        issues = sw["w"].tick()
        return f"{len(issues)} issue(s) open" if issues else "nothing stuck"
    return {
        "stuck-watch": dict(interval=int(stuck_watch.INTERVAL), mac=True, fn=stuck),
        "mem-reaper": dict(interval=120, mac=True, fn=mem_reaper.run),
        "stale-prompts": dict(interval=300, mac=True, fn=stale_prompts.run),
        "idle-simulators": dict(interval=600, mac=True, fn=idle_simulators.run),
        "docker-hygiene": dict(interval=86400, mac=False, fn=docker_hygiene.run),
    }


def load_status():
    try:
        return json.load(open(STATUS))
    except Exception:
        return {}


_LOCK = threading.Lock()


def save_status(st):
    with _LOCK:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATUS + ".tmp"
        json.dump(dict(st), open(tmp, "w"), indent=1)
        os.replace(tmp, STATUS)


def log(kind, msg):
    line = f"{datetime.now().isoformat(timespec='seconds')} {kind} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        open(LOG, "a").write(line + "\n")
    except OSError:
        pass


def run_check(name, spec, st, dry=False, force=False):
    t = time.time()
    try:
        summary, err = spec["fn"](dry=dry, force=force), None
    except Exception as e:                    # one broken check must not stop the others
        summary, err = None, f"{type(e).__name__}: {e}"
        log("ERROR", f"{name}: {err}")
    if not dry:
        st[name] = {"last_run": t, "ok": err is None, "summary": summary, "error": err, "interval": spec["interval"]}
        save_status(st)
    return summary or err


def cmd_run(args):
    once, dry = "--once" in args, "--dry-run" in args
    only = args[args.index("--check") + 1] if "--check" in args else None
    todo = {n: s for n, s in checks().items() if (DARWIN or not s["mac"]) and (only in (None, n))}
    if only and not todo:
        sys.exit(f"machine-health: unknown or macOS-only check {only!r} on this machine")
    st = load_status()
    if once:
        for n, s in todo.items():
            print(f"{n}: {run_check(n, s, st, dry=dry, force=True)}")
        return
    import faulthandler, signal
    faulthandler.register(signal.SIGUSR1, file=open(os.path.join(STATE_DIR, "stacks.txt"), "w"))   # kill -USR1 <pid> dumps thread stacks
    log("START", f"machine-health checks: {', '.join(todo)}")
    def loop(n, spec):       # one thread per check: a slow start (stuck-watch reads an hour of TCC log) blocks nobody
        while True:
            run_check(n, spec, st)
            time.sleep(spec["interval"])
    for n, spec in todo.items():
        threading.Thread(target=loop, args=(n, spec), daemon=True).start()
    while True:
        time.sleep(3600)


def ago(sec):
    return f"{int(sec)}s" if sec < 90 else f"{int(sec // 60)}m" if sec < 5400 else f"{sec / 3600:.1f}h" if sec < 172800 else f"{int(sec // 86400)}d"


def job_state():
    if not DARWIN:
        return "not applicable (macOS job)"
    for label in (PA_LABEL, LABEL):
        r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], capture_output=True, text=True)
        if r.returncode == 0:
            state = next((l.split("=")[1].strip() for l in r.stdout.splitlines() if l.strip().startswith("state =")), "?")
            pid = next((l.split("=")[1].strip() for l in r.stdout.splitlines() if l.strip().startswith("pid =")), "-")
            return f"{label}: {state}, pid {pid}"
    return "NOT INSTALLED (run: machine-health install)"


def cmd_status(args):
    st, now = load_status(), time.time()
    print(f"job:    {job_state()}")
    old = [l for l in OLD_LABELS if DARWIN and subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{l}"],
                                                             capture_output=True).returncode == 0]
    if old:
        print(f"WARNING old jobs still loaded (would run twice): {', '.join(old)}")
    print(f"config: {CONFIG}{'' if os.path.exists(CONFIG) else ' (not created yet)'}\nlog:    {LOG}\n")
    bad = False
    for name, s in checks().items():
        if s["mac"] and not DARWIN:
            print(f"  {name:15} n/a on this OS")
            continue
        r = st.get(name)
        if not r:
            print(f"  {name:15} NOT RUN YET")
            bad = True
            continue
        age = now - r["last_run"]
        running = age < max(3 * r["interval"], 180)
        flag = "running" if running and r["ok"] else "ERROR  " if not r["ok"] else "STALE  "
        bad |= flag != "running"
        print(f"  {name:15} {flag} last {ago(age)} ago (every {ago(r['interval'])}): {r['summary'] or r['error']}")
    print()
    try:
        tail = open(LOG).read().splitlines()[-5:]
        print("last log lines:\n  " + "\n  ".join(l[:200] for l in tail))
    except OSError:
        pass
    sys.exit(1 if bad else 0)


# ---------------------------------------------------------------- install
def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def uv_python():
    """The Python the old Stuck Watch app used, else uv's 3.12, else the system one."""
    try:
        cfg = json.load(open(os.path.join(HOME, "Applications/Personal Agent/Stuck Watch.app/Contents/Resources/pa-app.json")))
        if os.path.exists(cfg["python"]):
            return cfg["python"]
    except Exception:
        pass
    r = sh("uv", "python", "find", "3.12") if shutil.which("uv") else None
    return r.stdout.strip() if r and r.returncode == 0 and r.stdout.strip() else "/usr/bin/python3"


def pa_app():
    p = os.path.join(HOME, "Documents/personal/personal-agent/bin/pa-app")
    return p if os.path.exists(p) and os.path.isdir(os.path.join(HOME, "Applications/Personal Agent")) else None


def retire_old(pa):
    uid = os.getuid()
    if pa and os.path.isdir(os.path.join(HOME, "Applications/Personal Agent/Stuck Watch.app")):
        print("removing the old \"Stuck Watch\" app:", sh(pa, "remove", "Stuck Watch").stdout.strip()[:100])
    for label in OLD_LABELS:
        plist = os.path.join(HOME, "Library/LaunchAgents", label + ".plist")
        sh("launchctl", "bootout", f"gui/{uid}/{label}")
        if os.path.exists(plist):
            os.remove(plist)
            print("removed", plist)
    shutil.rmtree(os.path.join(HOME, ".local/share/stuck-watch"), ignore_errors=True)
    shutil.rmtree(os.path.join(HOME, ".local/share/mem-reaper"), ignore_errors=True)


def migrate_config():
    old = os.path.join(HOME, ".config/stuck-watch/config")
    if not os.path.exists(CONFIG):
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        if os.path.exists(old):
            shutil.copy(old, CONFIG)
        else:
            open(CONFIG, "w").write(open(os.path.join(HERE, "config.example")).read())
        os.chmod(CONFIG, 0o600)
        print("created", CONFIG)


def cmd_install(args):
    if not DARWIN:
        print("machine-health install: the job is macOS-only (stuck-watch, mem-reaper and stale-prompts watch macOS things).\n"
              "On Linux run `machine-health run --once --check docker-hygiene` (read-only report) from cron if wanted.")
        return
    uid = os.getuid()
    pa = pa_app()
    if "--check" in args:
        print(job_state())
        return
    if "--uninstall" in args:
        if pa:
            print(sh(pa, "remove", APP_NAME).stdout.strip())
        sh("launchctl", "bootout", f"gui/{uid}/{LABEL}")
        p = os.path.join(HOME, "Library/LaunchAgents", LABEL + ".plist")
        os.path.exists(p) and os.remove(p)
        print("removed")
        return
    py = uv_python()
    migrate_config()
    retire_old(pa)
    script = os.path.join(HERE, "machine_health.py")
    if pa:    # a named app, so prompts/Login Items say "Machine Health" (same pattern as the other jobs on this Mac)
        sh(pa, "remove", APP_NAME)
        r = sh(pa, "install", APP_NAME, script, "--keep-alive", "--copy", "--python", py)
        print(r.stdout.strip()[-300:], r.stderr.strip()[-300:])
    else:
        dest = os.path.join(HOME, ".local/share/machine-health")
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(HERE, dest, ignore=shutil.ignore_patterns("__pycache__", "tests"))
        plist = os.path.join(HOME, "Library/LaunchAgents", LABEL + ".plist")
        open(plist, "w").write(f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{dest}/machine_health.py</string><string>run</string></array>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>/tmp/machine-health.out</string><key>StandardErrorPath</key><string>/tmp/machine-health.out</string>
</dict></plist>''')
        sh("launchctl", "bootout", f"gui/{uid}/{LABEL}")
        print(sh("launchctl", "bootstrap", f"gui/{uid}", plist).stderr.strip())
    time.sleep(8)
    print(job_state())


def main():
    args = sys.argv[1:]
    cmd = args[0] if args and not args[0].startswith("-") else "run"
    rest = args[1:] if args and not args[0].startswith("-") else args
    {"run": cmd_run, "status": cmd_status, "install": cmd_install}.get(cmd) or sys.exit(__doc__)
    {"run": cmd_run, "status": cmd_status, "install": cmd_install}[cmd](rest)


if __name__ == "__main__":
    main()
