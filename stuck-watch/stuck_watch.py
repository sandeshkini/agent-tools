#!/usr/bin/env python3
"""Stuck Watch: notice when an AI agent on this Mac is silently stuck, and say so once.

Agents (Claude Code, OpenCode, Codex, cptr) run commands from background processes that can't
show macOS dialogs. When macOS wants to ask the user something (a TCC privacy prompt, the
com.apple.macl "App Data" check on a file another app downloaded, a password dialog), the
command just waits, forever, and the chat looks frozen. This watcher looks every ~60 s for:

  1. stuck agent commands   processes run by an agent's shell that have been alive > N minutes
                            with no CPU time growth (file operations: N=3 by default)
  2. TCC prompts            a privacy prompt that macOS raised (AUTHREQ_PROMPTING in the unified
                            log) and nobody answered after 2 minutes, for any app; and denials
                            (non-preflight) attributed to an agent process
  3. on-screen dialogs      permission/password windows (UserNotificationCenter, SecurityAgent,
                            coreautha, ...) still on screen after 2 minutes

and pushes one ntfy notification per distinct issue (again after 30 minutes if it persists),
plus a line in a local log. It only reads: it never kills a process or clicks anything.

Stdlib only (window list via ctypes into CoreGraphics; no Accessibility or Screen Recording
permission needed). Config: environment, then ~/.config/stuck-watch/config (KEY=VALUE lines).
See README.md for every key.

    stuck_watch.py            run forever (what the LaunchAgent does)
    stuck_watch.py --once     one pass, print what it sees, notify as usual (needs prior state
                              to judge CPU growth, so it only flags things that are clearly stuck)
    stuck_watch.py --dry-run  like the default, but log instead of notifying
"""
import ctypes
import json
import os
import plistlib
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime

HOME = os.path.expanduser("~")
CONFIG_FILE = os.path.join(HOME, ".config", "stuck-watch", "config")


# ------------------------------------------------------------------------------------ config
def load_config():
    cfg = {}
    try:
        for line in open(CONFIG_FILE):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    cfg.update({k: v for k, v in os.environ.items() if k.startswith(("STUCK_WATCH_", "NTFY_"))})
    return cfg


CFG = load_config()


def conf(key, default):
    v = CFG.get(key)
    if v is None or v == "":
        return default
    return type(default)(v) if not isinstance(default, str) else v


AGENTS = [a.strip() for a in conf("STUCK_WATCH_AGENTS", "claude,opencode,codex,cptr").split(",") if a.strip()]
FILE_MIN = conf("STUCK_WATCH_MINUTES", 3.0)            # file operations
OTHER_MIN = conf("STUCK_WATCH_OTHER_MINUTES", 15.0)    # any other command
PROMPT_MIN = conf("STUCK_WATCH_PROMPT_MINUTES", 2.0)   # TCC prompts + on-screen dialogs
REALERT_MIN = conf("STUCK_WATCH_REALERT_MINUTES", 30.0)
INTERVAL = conf("STUCK_WATCH_INTERVAL", 60.0)
CPU_EPS = conf("STUCK_WATCH_CPU_EPSILON", 0.05)        # seconds of CPU that still count as "idle"
LOG_FILE = os.path.expanduser(conf("STUCK_WATCH_LOG", "~/Library/Logs/stuck-watch.log"))
STATE_FILE = os.path.expanduser(conf("STUCK_WATCH_STATE", "~/.local/state/stuck-watch/state.json"))
TITLE = conf("STUCK_WATCH_TITLE", "Stuck Watch")
TCC_ENABLED = conf("STUCK_WATCH_TCC", "1") != "0"
WINDOWS_ENABLED = conf("STUCK_WATCH_WINDOWS", "1") != "0"
DIALOG_OWNERS = [o.strip() for o in conf(
    "STUCK_WATCH_DIALOG_OWNERS",
    "UserNotificationCenter,SecurityAgent,coreautha,tccd,universalAccessAuthWarn,"
    "CoreServicesUIAgent,AuthenticationServicesAgent").split(",") if o.strip()]
# Commands that legitimately sit idle (an agent waiting on purpose, or a viewer/follower).
IGNORE = re.compile(conf(
    "STUCK_WATCH_IGNORE",
    r"^(sleep|tail +-[fF]|less|more|man|vim?|nano|top|htop|watch|caffeinate|"
    r"(/usr/bin/)?log +stream|ssh|mosh|tmux|screen|script|fswatch|docker( compose)? +logs|"
    r"kubectl +logs|wait)\b"))
# File operations: the commands TCC / com.apple.macl hangs actually hit. Flagged at FILE_MIN.
FILE_OPS = {
    "rm", "mv", "cp", "ditto", "rsync", "xattr", "ln", "touch", "chmod", "chown", "chflags",
    "cat", "head", "tail", "ls", "find", "du", "stat", "mdls", "mdfind", "open", "tar", "zip",
    "unzip", "gzip", "gunzip", "shasum", "md5", "file", "grep", "rg", "osascript", "sqlite3",
    "plutil", "defaults", "textutil", "sips", "qlmanage", "security", "tccutil", "hdiutil",
    "diskutil", "mkdir", "rmdir", "readlink", "realpath", "wc", "sort", "pdftotext", "soffice",
}
# Commands that sit at the end of a pipe (`cmd | tail -3`) wait on the command feeding
# them, not on a file. While a sibling in the same pipeline is alive, judge that one instead.
PIPE_FILTERS = {"tail", "head", "grep", "rg", "wc", "sort", "uniq", "tee", "awk", "sed", "cut",
                "jq", "less", "more", "tr", "column", "xargs"}
PROTECTED = re.compile(r"(Downloads|Desktop|Documents|Library/Mobile Documents|iCloud|/Volumes/|"
                       r"Pictures|Photos|Movies|Music|Library/Mail|Library/Messages|Library/Safari)")


def mcp_tools_env():
    """EnvironmentVariables of an installed agent-tools mcp-tools LaunchAgent, if any."""
    agents = os.path.join(HOME, "Library", "LaunchAgents")
    for name in ("com.sandesh.mcp-tools.plist", "com.sandesh.pa.agent-tools-mcp.plist"):
        try:
            env = plistlib.load(open(os.path.join(agents, name), "rb")).get("EnvironmentVariables", {})
        except Exception:
            continue
        if env:
            return env
    return {}


def host_label():
    v = conf("STUCK_WATCH_HOST", "") or CFG.get("COMPUTER_LABEL") or os.environ.get("COMPUTER_LABEL") \
        or mcp_tools_env().get("COMPUTER_LABEL")
    if v:
        return v
    try:
        return subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or socket.gethostname().split(".")[0]
    except Exception:
        return socket.gethostname().split(".")[0]


HOST = host_label()


def ntfy_config():
    """NTFY_URL/TOPIC/TOKEN from env or the config file; else borrow them from an installed
    agent-tools mcp-tools LaunchAgent (the same bus its `notify` tool pushes to)."""
    url, topic, token = CFG.get("NTFY_URL"), CFG.get("NTFY_TOPIC"), CFG.get("NTFY_TOKEN")
    if not url:
        env = mcp_tools_env()
        url, topic, token = env.get("NTFY_URL"), env.get("NTFY_TOPIC"), env.get("NTFY_TOKEN")
    return url, topic or "aibo", token


# ----------------------------------------------------------------------------- log + notify
def log(kind, msg):
    line = f"{datetime.now().isoformat(timespec='seconds')} {kind} {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


DRY_RUN = "--dry-run" in sys.argv or conf("STUCK_WATCH_DRY_RUN", "0") == "1"


def notify(message, priority=4):
    if DRY_RUN:
        log("DRY-RUN", f"would notify: {message}")
        return True
    url, topic, token = ntfy_config()
    if not url:
        log("WARN", "no NTFY_URL configured; logged only")
        return False
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps({"topic": topic, "title": TITLE, "message": message, "priority": priority,
                       "tags": ["hourglass"]}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url.rstrip("/") + "/", data=body, method="POST",
                                                      headers=headers), timeout=10).read()
        return True
    except Exception as e:
        log("WARN", f"ntfy failed: {e}")
        return False


class Alerts:
    """One notification per distinct issue; again after REALERT_MIN if it's still there."""

    def __init__(self):
        self.sent = {}
        try:
            self.sent = json.load(open(STATE_FILE)).get("sent", {})
        except Exception:
            pass

    def fire(self, key, message, priority=4):
        now = time.time()
        last = self.sent.get(key)
        if last and now - last < REALERT_MIN * 60:
            return False
        log("ALERT" if not last else "REALERT", f"[{key}] {message}")
        notify(message, priority)
        self.sent[key] = now
        self.save()
        return True

    def forget_except(self, live_keys):
        """Drop keys of issues that went away (so a new occurrence alerts again right away)."""
        gone = [k for k in self.sent if k not in live_keys]
        for k in gone:
            log("CLEARED", f"[{k}]")
            del self.sent[k]
        if gone:
            self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            tmp = STATE_FILE + ".tmp"
            json.dump({"sent": self.sent}, open(tmp, "w"))
            os.replace(tmp, STATE_FILE)
        except OSError:
            pass


def ago(seconds):
    return f"{int(seconds // 60)}m" if seconds >= 60 else f"{int(seconds)}s"


# ------------------------------------------------------------------------------- redaction
SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=\-]{6,}"), r"\1 ***"),
    (re.compile(r"(?i)\b(bearer|token|secret|password|passwd|pwd|api[_-]?key|apikey|auth|"
                r"authorization|access[_-]?key|client[_-]?secret)(\s*[=:]\s*|\s+)(\S+)"), r"\1\2***"),
    (re.compile(r"(?i)(--?(?:token|password|passwd|secret|key|api-key|auth)[= ])(\S+)"), r"\1***"),
    (re.compile(r"(://[^/\s:@]+:)[^@\s/]+@"), r"\1***@"),
    (re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,}|gh[pousr]_[A-Za-z0-9]{12,}|github_pat_\w{12,}|"
                r"xox[abposr]-[A-Za-z0-9\-]{8,}|AKIA[0-9A-Z]{12,}|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-.]+|"
                r"tk_[A-Za-z0-9]{12,})"), "***"),
]
LONG_OPAQUE = re.compile(r"(?<![/\w.])(?=[A-Za-z0-9_\-+=]*\d)(?=[A-Za-z0-9_\-+=]*[A-Za-z])[A-Za-z0-9_\-+=]{24,}(?![/\w])")


def redact(cmd):
    for pat, rep in SECRET_PATTERNS:
        cmd = pat.sub(rep, cmd)
    return LONG_OPAQUE.sub("***", cmd)


def shorten(cmd, limit=140):
    """Redact, put ~ for home, shorten long paths to …/<last two parts>, then truncate."""
    cmd = redact(cmd).replace(HOME, "~")
    out = []
    for tok in cmd.split(" "):
        parts = tok.split("/")
        if len(parts) > 4 and len(tok) > 30:
            tok = "…/" + "/".join(parts[-2:])
        out.append(tok)
    s = " ".join(out)
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ------------------------------------------------------------------------- 1. stuck commands
SHELLS = {"sh", "bash", "zsh", "dash", "fish", "ksh"}


def parse_etime(s):
    """ps etime: [[dd-]hh:]mm:ss -> seconds"""
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [int(float(p)) for p in s.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, sec = parts
    return days * 86400 + h * 3600 + m * 60 + sec


def parse_cputime(s):
    """ps time: [hh:]mm:ss.cc -> seconds"""
    parts = s.split(":")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def snapshot():
    out = subprocess.run(["/bin/ps", "-axww", "-o", "pid=,ppid=,state=,etime=,time=,command="],
                         capture_output=True, text=True, timeout=20).stdout
    procs = {}
    for line in out.splitlines():
        p = line.split(None, 5)
        if len(p) < 6:
            continue
        try:
            procs[int(p[0])] = {"pid": int(p[0]), "ppid": int(p[1]), "state": p[2],
                                "etime": parse_etime(p[3]), "cpu": parse_cputime(p[4]), "cmd": p[5]}
        except ValueError:
            continue
    return procs


def argv0(cmd):
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    return toks


INTERPRETERS = re.compile(r"^(node|bun|deno|python[\d.]*|uv|uvx|npx)$")


def is_agent(cmd):
    """argv[0] is an agent, or an interpreter running one (`python …/bin/cptr run`, `node …/codex`)."""
    toks = argv0(cmd)
    if not toks:
        return False
    if os.path.basename(toks[0]) in AGENTS:
        return True
    if INTERPRETERS.match(os.path.basename(toks[0])):
        script = next((t for t in toks[1:3] if not t.startswith("-")), "")
        return os.path.basename(script) in AGENTS
    return False


def agent_name(cmd):
    return next((os.path.basename(t) for t in argv0(cmd)[:3] if os.path.basename(t) in AGENTS), "agent")


def is_shell_c(cmd):
    toks = argv0(cmd)
    return bool(toks) and os.path.basename(toks[0]).lstrip("-") in SHELLS and "-c" in toks[1:4]


def command_name(cmd):
    toks = argv0(cmd)
    if not toks:
        return ""
    name = os.path.basename(toks[0])
    if name in ("env", "nice", "nohup", "time", "timeout", "gtimeout", "sudo", "caffeinate") and len(toks) > 1:
        rest = [t for t in toks[1:] if not t.startswith("-") and "=" not in t]
        if rest:
            name = os.path.basename(rest[0])
    return name


def listening(pid):
    try:
        out = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=10).stdout
        return bool(out.strip())
    except Exception:
        return False


class StuckCommands:
    def __init__(self):
        self.idle_since = {}   # (pid, cmd) -> (cpu at last change, time of last change)
        self.me = os.getpid()

    def scan(self, procs, tcc_pending_pids):
        """Return [(key, message)] for agent commands idle past their threshold."""
        now = time.time()
        kids = {}
        for p in procs.values():
            kids.setdefault(p["ppid"], []).append(p["pid"])

        # my own subtree (when run by hand from inside an agent) is never "stuck"
        mine, stack = set(), [self.me]
        while stack:
            pid = stack.pop()
            mine.add(pid)
            stack.extend(kids.get(pid, []))

        # processes run by an agent's shell: below an agent, under a `sh -c`/`zsh -c`
        candidates = {}   # pid -> agent root pid
        for root in [p for p in procs.values() if is_agent(p["cmd"])]:
            stack = [(c, False) for c in kids.get(root["pid"], [])]
            while stack:
                pid, under_shell = stack.pop()
                p = procs.get(pid)
                if not p or pid in mine or is_agent(p["cmd"]):
                    continue   # a nested agent is scanned as its own root
                shell = is_shell_c(p["cmd"])
                if under_shell and not shell:
                    candidates[pid] = root["pid"]
                stack.extend((c, under_shell or shell) for c in kids.get(pid, []))

        live = set()
        found = []
        for pid, root in candidates.items():
            p = procs[pid]
            if p["state"].startswith("Z"):
                continue
            if any(c in candidates and not procs[c]["state"].startswith("Z") for c in kids.get(pid, [])):
                continue   # report the deepest process: that's the one actually waiting
            if command_name(p["cmd"]) in PIPE_FILTERS and any(
                s != pid and s in procs and not procs[s]["state"].startswith("Z")
                for s in kids.get(p["ppid"], [])
            ):
                continue   # end of a pipe; its feeder is alive and is judged on its own
            k = (pid, p["cmd"])
            live.add(k)
            prev = self.idle_since.get(k)
            if prev is None:
                # first sight: a long-lived process with almost no CPU at all has been idle since start
                since = now - p["etime"] if p["cpu"] < 4 * CPU_EPS else now
                self.idle_since[k] = (p["cpu"], since)
                prev = self.idle_since[k]
            elif p["cpu"] - prev[0] > CPU_EPS:
                self.idle_since[k] = (p["cpu"], now)
                continue
            idle = now - prev[1]
            name = command_name(p["cmd"])
            if IGNORE.search(_bare(p["cmd"])):
                continue
            # python is a file op only when it names a protected folder (else it may be a daemon)
            fileop = name in FILE_OPS or (name.startswith("python") and bool(PROTECTED.search(p["cmd"])))
            limit = (FILE_MIN if fileop else OTHER_MIN) * 60
            if idle < limit or p["etime"] < limit:
                continue
            if listening(pid):
                continue   # a server an agent started on purpose
            short = shorten(p["cmd"])
            if pid in tcc_pending_pids:
                why = f"macOS permission prompt pending: {tcc_pending_pids[pid]}"
            elif fileop and PROTECTED.search(p["cmd"]):
                why = "waiting on macOS permission?"
            elif fileop:
                why = "waiting on a file or macOS permission?"
            else:
                why = "waiting on input or a dialog?"
            agent = agent_name(procs[root]["cmd"])
            found.append((f"cmd:{pid}", f"Stuck {ago(idle)} on {HOST}: {short} ({why}) [{agent}, pid {pid}]"))
        self.idle_since = {k: v for k, v in self.idle_since.items() if k in live}
        return found


def _bare(cmd):
    """the command line with its program reduced to a basename, for the IGNORE regex"""
    toks = cmd.split(" ", 1)
    return os.path.basename(toks[0]) + (" " + toks[1] if len(toks) > 1 else "")


# ---------------------------------------------------------------------------------- 2. TCC
TCC_PREDICATE = ('subsystem == "com.apple.TCC" AND (eventMessage BEGINSWITH "AUTHREQ_PROMPTING" '
                 'OR eventMessage BEGINSWITH "AUTHREQ_RESULT" OR eventMessage BEGINSWITH "AUTHREQ_CTX" '
                 'OR eventMessage BEGINSWITH "AUTHREQ_ATTRIBUTION")')
RX_MSGID = re.compile(r"msgID=([\w.]+)")


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def service_name(s):
    s = (s or "").replace("kTCCService", "")
    return {"SystemPolicyAllFiles": "Full Disk Access", "SystemPolicyDownloadsFolder": "Downloads",
            "SystemPolicyDocumentsFolder": "Documents", "SystemPolicyDesktopFolder": "Desktop",
            "SystemPolicyNetworkVolumes": "network volumes", "SystemPolicyRemovableVolumes": "removable volumes",
            "FileProviderDomain": "iCloud Drive / file provider", "Ubiquity": "iCloud Drive",
            "SystemPolicyAppData": "another app's data", "ScreenCapture": "Screen Recording",
            "PostEvent": "Accessibility (input)", "ListenEvent": "Input Monitoring",
            "AppleEvents": "Automation"}.get(s, s or "?")


class TCCWatch:
    """Follows the TCC log with `/usr/bin/log stream` (absolute path: in zsh `log` is a builtin).

    A prompt shows up as AUTHREQ_PROMPTING msgID=X; when the user answers, tccd logs
    AUTHREQ_RESULT msgID=X. A PROMPTING with no RESULT after PROMPT_MIN = a prompt nobody is
    answering (seen: a Photos prompt from a background app that waited 14 h)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.ctx = {}        # (tccd pid, msgID) -> {service, preflight, attribution}
        self.pending = {}    # (tccd pid, msgID) -> {t, service, who, pid, agent}
        self.denials = []    # [(key, message)] queued for the main loop
        self.agent_pids = set()

    def start(self):
        self._seed()
        threading.Thread(target=self._follow, daemon=True).start()

    def _seed(self):
        """Catch prompts that started before we did (last hour)."""
        try:
            out = subprocess.run(["/usr/bin/log", "show", "--last", "1h", "--style", "ndjson",
                                  "--predicate", TCC_PREDICATE], capture_output=True, text=True,
                                 timeout=180).stdout
            for line in out.splitlines():
                self._handle(line, seeding=True)
        except Exception as e:
            log("WARN", f"TCC seed failed: {e}")

    def _follow(self):
        while True:
            try:
                proc = subprocess.Popen(["/usr/bin/log", "stream", "--style", "ndjson", "--predicate",
                                         TCC_PREDICATE], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, bufsize=1)
                for line in proc.stdout:
                    self._handle(line)
                proc.wait()
            except Exception as e:
                log("WARN", f"TCC stream error: {e}")
            time.sleep(10)

    def _handle(self, line, seeding=False):
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            e = json.loads(line)
        except ValueError:
            return
        msg = e.get("eventMessage", "")
        m = RX_MSGID.search(msg)
        if not m:
            return
        key = (e.get("processID"), m.group(1))
        ts = _ts(e.get("timestamp")) or time.time()
        with self.lock:
            if msg.startswith("AUTHREQ_CTX"):
                svc = re.search(r"service=(\w+)", msg)
                pre = re.search(r"preflight=(\w+)", msg)
                self.ctx[key] = {"service": svc and svc.group(1), "preflight": pre and pre.group(1), "t": ts}
            elif msg.startswith("AUTHREQ_ATTRIBUTION"):
                self.ctx.setdefault(key, {"t": ts})["attribution"] = msg
            elif msg.startswith("AUTHREQ_PROMPTING"):
                svc = re.search(r"service=(\w+)", msg)
                ident = re.search(r"Resp:\{TCCDProcess: identifier=([^,]+), pid=(\d+)", msg)
                rpath = re.search(r"responsible_path=([^,]+)", msg)
                who = ident.group(1) if ident and ident.group(1) != "-" else (
                    os.path.basename(rpath.group(1)) if rpath else "?")
                if rpath and ".app/" in rpath.group(1):
                    who = os.path.basename(rpath.group(1).split(".app/")[0])
                attribution = self.ctx.get(key, {}).get("attribution", "") + msg
                self.pending[key] = {"t": ts, "service": svc and svc.group(1), "who": who,
                                     "pid": int(ident.group(2)) if ident else None,
                                     "agent": self._agentish(attribution)}
            elif msg.startswith("AUTHREQ_RESULT"):
                self.pending.pop(key, None)
                c = self.ctx.pop(key, {})
                val = re.search(r"authValue=(\d+)", msg)
                if (not seeding and val and val.group(1) == "0" and c.get("preflight") == "no"
                        and self._agentish(c.get("attribution", ""))):
                    who = self._who(c.get("attribution", ""))
                    self.denials.append((f"tccdeny:{who}:{c.get('service')}",
                                         f"macOS denied {service_name(c.get('service'))} to {who} on {HOST} "
                                         f"(agent command may fail or hang; grant it in System Settings › Privacy)"))
            # keep ctx small: drop entries older than 10 min
            if len(self.ctx) > 2000:
                cutoff = time.time() - 600
                self.ctx = {k: v for k, v in self.ctx.items() if v.get("t", 0) > cutoff}

    def _agentish(self, text):
        if not text:
            return False
        for pid in re.findall(r"pid=(\d+)", text):
            if int(pid) in self.agent_pids:
                return True
        paths = " ".join(re.findall(r"(?:responsible_path|binary_path|identifier)=([^,}]+)", text)).lower()
        return any(re.search(rf"(^|[/. -]){re.escape(a.lower())}([/. -]|$)", paths) for a in AGENTS) or \
            "claude-code" in paths

    @staticmethod
    def _who(attr):
        m = re.search(r"accessing=\{TCCDProcess: identifier=([^,]+)", attr) or \
            re.search(r"responsible=\{TCCDProcess: identifier=([^,]+)", attr)
        return m.group(1) if m else "an agent process"

    def check(self, procs):
        """Return ([(key, message)], {pid: service} of pending prompts) and drain denials."""
        now = time.time()
        # agent pids + their subtrees, so attribution by pid works
        kids = {}
        for p in procs.values():
            kids.setdefault(p["ppid"], []).append(p["pid"])
        agent_pids, stack = set(), [p["pid"] for p in procs.values() if is_agent(p["cmd"])]
        while stack:
            pid = stack.pop()
            if pid not in agent_pids:
                agent_pids.add(pid)
                stack.extend(kids.get(pid, []))
        found, pend = [], {}
        with self.lock:
            self.agent_pids = agent_pids
            for key, p in list(self.pending.items()):
                if p["pid"] and not pid_alive(p["pid"]):
                    del self.pending[key]   # the app gave up / quit: the prompt is gone
                    continue
                if p["pid"]:
                    pend[p["pid"]] = service_name(p["service"])
                age = now - p["t"]
                if age >= PROMPT_MIN * 60:
                    tag = " (agent)" if p["agent"] or p["pid"] in agent_pids else ""
                    found.append((f"tcc:{key[0]}:{key[1]}",
                                  f"Permission prompt waiting {ago(age)} on {HOST}: "
                                  f"{p['who']}{tag} wants {service_name(p['service'])}"))
            found += self.denials
            self.denials = []
        return found, pend


def _ts(s):
    if not s:
        return None
    try:
        return datetime.strptime(s[:26], "%Y-%m-%d %H:%M:%S.%f").timestamp()
    except ValueError:
        return None


# ------------------------------------------------------------------- 3. on-screen dialogs
class Windows:
    """On-screen windows via CGWindowListCopyWindowInfo (ctypes; no Accessibility needed.
    Without Screen Recording the titles come back empty, owner names still work)."""

    def __init__(self):
        self.first_seen = {}
        V = ctypes.c_void_p
        self.cf = cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.cg = cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGWindowListCopyWindowInfo.restype = V
        cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetCount.argtypes = [V]
        cf.CFArrayGetValueAtIndex.restype = V
        cf.CFArrayGetValueAtIndex.argtypes = [V, ctypes.c_long]
        cf.CFDictionaryGetValue.restype = V
        cf.CFDictionaryGetValue.argtypes = [V, V]
        cf.CFStringCreateWithCString.restype = V
        cf.CFStringCreateWithCString.argtypes = [V, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [V, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFNumberGetValue.restype = ctypes.c_bool
        cf.CFNumberGetValue.argtypes = [V, ctypes.c_int, V]
        cf.CFGetTypeID.restype = ctypes.c_ulong
        cf.CFGetTypeID.argtypes = [V]
        cf.CFStringGetTypeID.restype = ctypes.c_ulong
        cf.CFRelease.argtypes = [V]
        self.keys = {k: cf.CFStringCreateWithCString(None, k.encode(), 0x08000100) for k in
                     ("kCGWindowOwnerName", "kCGWindowOwnerPID", "kCGWindowLayer", "kCGWindowName",
                      "kCGWindowNumber", "kCGWindowBounds", "Width", "Height")}

    def _str(self, d, k):
        v = self.cf.CFDictionaryGetValue(d, self.keys[k])
        if not v or self.cf.CFGetTypeID(v) != self.cf.CFStringGetTypeID():
            return ""
        buf = ctypes.create_string_buffer(1024)
        self.cf.CFStringGetCString(v, buf, 1024, 0x08000100)
        return buf.value.decode("utf-8", "replace")

    def _num(self, d, k):
        v = self.cf.CFDictionaryGetValue(d, self.keys[k])
        if not v:
            return 0.0
        x = ctypes.c_double()
        self.cf.CFNumberGetValue(v, 13, ctypes.byref(x))   # kCFNumberDoubleType
        return x.value

    def list(self):
        arr = self.cg.CGWindowListCopyWindowInfo(1 | 16, 0)   # on-screen only, minus desktop
        if not arr:
            return []
        wins = []
        try:
            for i in range(self.cf.CFArrayGetCount(arr)):
                d = self.cf.CFArrayGetValueAtIndex(arr, i)
                b = self.cf.CFDictionaryGetValue(d, self.keys["kCGWindowBounds"])
                w = h = 0.0
                if b:
                    w, h = self._num(b, "Width"), self._num(b, "Height")
                wins.append({"owner": self._str(d, "kCGWindowOwnerName"), "title": self._str(d, "kCGWindowName"),
                             "num": int(self._num(d, "kCGWindowNumber")), "layer": int(self._num(d, "kCGWindowLayer")),
                             "w": w, "h": h})
        finally:
            self.cf.CFRelease(arr)
        return wins

    def check(self):
        now = time.time()
        wins = self.list()
        hits = {}
        settings = [w for w in wins if w["owner"] == "System Settings" and w["layer"] == 0]
        for w in wins:
            title = w["title"]
            reason = None
            if w["owner"] in DIALOG_OWNERS and w["w"] > 50 and w["h"] > 50:
                reason = "Password prompt" if w["owner"] in ("SecurityAgent", "coreautha") else "Dialog"
                reason += f" waiting in {w['owner']}"
            elif re.search(r"wants to (access|make changes|use)|would like to access|password", title, re.I):
                reason = f"Dialog waiting in {w['owner']}"
            elif w["owner"] == "System Settings" and len(settings) > 1 and w is not max(
                    settings, key=lambda s: s["w"] * s["h"]) and w["layer"] == 0 and w["w"] < 700:
                reason = "Password/permission sheet waiting in System Settings"
            if reason:
                hits[w["num"]] = reason + (f": \"{title[:60]}\"" if title else "")
        self.first_seen = {n: self.first_seen.get(n, now) for n in hits}
        return [(f"win:{n}", f"{hits[n]} on {HOST} ({ago(now - t)})")
                for n, t in self.first_seen.items() if now - t >= PROMPT_MIN * 60]


# ------------------------------------------------------------------------------------- main
def main():
    once = "--once" in sys.argv
    log("START", f"host={HOST} agents={','.join(AGENTS)} file={FILE_MIN}m other={OTHER_MIN}m "
                 f"prompt={PROMPT_MIN}m realert={REALERT_MIN}m interval={INTERVAL}s "
                 f"ntfy={'yes' if ntfy_config()[0] else 'no'}{' DRY-RUN' if DRY_RUN else ''}")
    alerts = Alerts()
    stuck = StuckCommands()
    tcc = TCCWatch() if TCC_ENABLED else None
    if tcc:
        if once:
            tcc._seed()
        else:
            tcc.start()
    try:
        wins = Windows() if WINDOWS_ENABLED else None
    except OSError as e:
        log("WARN", f"window list unavailable: {e}")
        wins = None
    while True:
        issues = []
        try:
            procs = snapshot()
            tcc_issues, pending = tcc.check(procs) if tcc else ([], {})
            cmd_issues = stuck.scan(procs, pending)
            issues = cmd_issues + tcc_issues + (wins.check() if wins else [])
        except Exception as e:
            log("ERROR", f"scan failed: {e!r}")
        live = set()
        for key, message in issues:
            live.add(key)
            alerts.fire(key, message)
        # denials are one-off events; keep their dedupe entry until REALERT_MIN passes
        now = time.time()
        live |= {k for k, t in alerts.sent.items() if k.startswith("tccdeny:") and now - t < REALERT_MIN * 60}
        alerts.forget_except(live)
        if once:
            if not issues:
                print("nothing stuck")
            return
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
