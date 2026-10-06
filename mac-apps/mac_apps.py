#!/usr/bin/env python3
"""mac-apps: run cptr, the cptr watchdog and Stuck Watch as properly named macOS apps.

Run through ./install.sh (it checks for the Xcode Command Line Tools first). See README.md.

    install.sh [--check]        what's installed, what would change; changes nothing (default)
    install.sh --cptr           wrap cptr's LaunchAgent in cptr.app (Full Disk Access first, then switch)
    install.sh --watchdog       wrap the cptr watchdog in "cptr Watchdog.app"
    install.sh --stuck-watch    wrap Stuck Watch in "Stuck Watch.app"
    install.sh --all            watchdog, Stuck Watch, then cptr (cptr last: its restart can end your session)
    install.sh --rollback       put back the LaunchAgents mac-apps changed (from its backups)

    install.sh build "<Name>" [--bundle-id ID] -- <argv...>    build any app (launcher runs argv)
    install.sh probe "<Name>" <path...>                        open paths as that app, via launchd

Environment overrides:
    MAC_APPS_DIR             apps folder (default ~/Applications/Personal Agent if it exists,
                             else ~/Applications/Agent Apps)
    MAC_APPS_BUNDLE_PREFIX   bundle id prefix (default local.agent-apps)
    MAC_APPS_SIGN_IDENTITY   codesign identity (default "-", ad-hoc)
    MAC_APPS_CPTR_LABEL      pick cptr's LaunchAgent label if more than one job runs cptr
    MAC_APPS_CPTR_PORT       cptr's port, if detection gets it wrong
    NTFY_URL / NTFY_TOPIC / NTFY_TOKEN   push the result (else Stuck Watch's / mcp-tools' settings)

Python 3.9+ standard library only (the Command Line Tools' /usr/bin/python3 is enough).
"""
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path.home()
UID = os.getuid()
DOMAIN = f"gui/{UID}"
AGENTS = Path(os.environ.get("MAC_APPS_AGENTS_DIR") or HOME / "Library" / "LaunchAgents")   # override: tests
BACKUPS = AGENTS / ".mac-apps-backup"
STATE = HOME / ".local" / "state" / "mac-apps"
LOG = HOME / "Library" / "Logs" / "mac-apps.log"
_pa = HOME / "Applications" / "Personal Agent"
APPS = Path(os.environ.get("MAC_APPS_DIR") or (_pa if _pa.is_dir() else HOME / "Applications" / "Agent Apps"))
PREFIX = os.environ.get("MAC_APPS_BUNDLE_PREFIX", "local.agent-apps")
SIGN_ID = os.environ.get("MAC_APPS_SIGN_IDENTITY", "-")
CONFIG_NAME = "mac-apps.json"        # in Contents/Resources: marks an app this tool built
TCC_DB = "/Library/Application Support/com.apple.TCC/TCC.db"
USER_TCC_DB = str(HOME / "Library/Application Support/com.apple.TCC/TCC.db")
FDA_URL = "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_AllFiles"
NAMES = {"cptr": "cptr", "watchdog": "cptr Watchdog", "stuck-watch": "Stuck Watch"}

# ------------------------------------------------------------------------------------ launcher
LAUNCHER_C = r'''
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
/* Built by agent-tools/mac-apps. Runs CMD (+ any extra arguments) as a child and waits, so macOS
   treats this app as the responsible process: privacy prompts name the app and grants belong to it. */
static const char *CMD[] = { @ARGV@ NULL };
static volatile sig_atomic_t child = 0;
static void forward(int sig) { if (child > 0) kill((pid_t)child, sig); }

/* --probe PATH...: open each path once, as this app (directories are listed, files read). */
static int probe(int n, char **paths) {
    int bad = 0;
    for (int i = 0; i < n; i++) {
        const char *p = paths[i];
        DIR *d = opendir(p);
        if (d) {
            errno = 0; struct dirent *e = readdir(d); int er = errno; closedir(d);
            if (e || er == 0) { printf("probe ok %s\n", p); continue; }
            errno = er;
        } else if (errno == ENOTDIR) {
            int fd = open(p, O_RDONLY);
            if (fd >= 0) {
                char c; ssize_t r = read(fd, &c, 1); int er = errno; close(fd);
                if (r >= 0) { printf("probe ok %s\n", p); continue; }
                errno = er;
            }
        }
        printf("probe denied %s: %s\n", p, strerror(errno));
        bad = 1;
    }
    fflush(stdout);
    return bad;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--probe") == 0) return probe(argc - 2, argv + 2);
    int n = 0; while (CMD[n]) n++;
    char **args = calloc((size_t)(n + argc + 1), sizeof(char *));
    if (!args) return 111;
    int k = 0;
    for (int i = 0; i < n; i++) args[k++] = (char *)CMD[i];
    for (int i = 1; i < argc; i++) args[k++] = argv[i];
    args[k] = NULL;

    /* Block the forwarded signals until the child's pid is known, so none is lost. */
    int sigs[] = { SIGTERM, SIGINT, SIGHUP };
    sigset_t block, old, def;
    sigemptyset(&block); sigemptyset(&def);
    for (int i = 0; i < 3; i++) { sigaddset(&block, sigs[i]); sigaddset(&def, sigs[i]); }
    sigprocmask(SIG_BLOCK, &block, &old);
    struct sigaction sa; memset(&sa, 0, sizeof sa);
    sa.sa_handler = forward; sigemptyset(&sa.sa_mask);
    for (int i = 0; i < 3; i++) sigaction(sigs[i], &sa, NULL);

    posix_spawnattr_t at; posix_spawnattr_init(&at);
    posix_spawnattr_setsigmask(&at, &old);
    posix_spawnattr_setsigdefault(&at, &def);
    posix_spawnattr_setflags(&at, POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF);
    pid_t pid;
    int rc = posix_spawn(&pid, args[0], NULL, &at, args, environ);
    if (rc != 0) { fprintf(stderr, "%s: cannot start %s: %s\n", argv[0], args[0], strerror(rc)); return 112; }
    child = pid;
    sigprocmask(SIG_SETMASK, &old, NULL);

    int st;
    while (waitpid(pid, &st, 0) < 0) { if (errno != EINTR) return 1; }
    if (WIFEXITED(st)) return WEXITSTATUS(st);
    if (WIFSIGNALED(st)) return 128 + WTERMSIG(st);
    return 1;
}
'''


# --------------------------------------------------------------------------------------- utils
def tilde(s):
    s = str(s)
    return "~" + s[len(str(HOME)):] if s.startswith(str(HOME)) else s


def log(msg, quiet=False):
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    if not quiet:
        print(msg, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def die(msg):
    print(f"mac-apps: {msg}", file=sys.stderr)
    sys.exit(1)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def launchctl(*a):
    return subprocess.run(["launchctl", *a], capture_output=True, text=True)


def job_state(label):
    """state/pid/program/last exit of a loaded job (top-level fields only), or None if not loaded."""
    r = launchctl("print", f"{DOMAIN}/{label}")
    if r.returncode:
        return None
    out = {}
    for key in ("state", "pid", "program", "last exit code"):
        m = re.search(rf"^\t{key} = (.+)$", r.stdout, re.M)
        out[key] = m.group(1).strip() if m else None
    return out


def bootout(label, wait=15):
    launchctl("bootout", f"{DOMAIN}/{label}")
    for _ in range(wait * 2):
        if job_state(label) is None:
            return True
        time.sleep(0.5)
    return False


def bootstrap(plist):
    r = launchctl("bootstrap", DOMAIN, str(plist))
    return r.returncode == 0, r.stderr.strip()


def load_plist(p):
    with open(p, "rb") as f:
        return plistlib.load(f)


def write_plist(p, job):
    p = Path(p)
    mode = p.stat().st_mode & 0o777 if p.exists() else 0o644
    tmp = p.with_suffix(".mac-apps-tmp")
    with open(tmp, "wb") as f:
        plistlib.dump(job, f)
    os.chmod(tmp, mode)
    os.replace(tmp, p)


def argv_of(job):
    a = list(job.get("ProgramArguments") or [])
    if job.get("Program"):
        a = [job["Program"]] + a[1:] if a else [job["Program"]]
    return a


def http_code(port, path, timeout=5):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def healthy(port):
    """cptr answers: /health 200, or / with a page or a login wall (password-mode cptr)."""
    if http_code(port, "/health") == 200:
        return True
    c = http_code(port, "/")
    return c is not None and (200 <= c < 400 or c in (401, 403))


def descendants(pid):
    rows = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True).stdout.split()
    kids = {}
    for p, pp in zip(rows[::2], rows[1::2]):
        kids.setdefault(pp, []).append(p)
    out, todo = [], [str(pid)]
    while todo:
        p = todo.pop()
        out.append(p)
        todo += kids.get(p, [])
    return out


def listening_ports(pid):
    if not pid:
        return []
    r = subprocess.run(["lsof", "-nP", "-a", "-iTCP", "-sTCP:LISTEN", "-p", ",".join(descendants(pid))],
                       capture_output=True, text=True)
    return sorted({int(m) for m in re.findall(r":(\d+) \(LISTEN\)", r.stdout)})


def port_pids(port):
    r = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    return [p for p in r.stdout.split() if p.strip()]


def which(cmd, path_env=None):
    return shutil.which(cmd, path=path_env or os.environ.get("PATH"))


# ------------------------------------------------------------------------------- app building
def have_cc():
    if subprocess.run(["xcode-select", "-p"], capture_output=True).returncode:
        return None
    r = subprocess.run(["xcrun", "--find", "cc"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def cstr(s):
    out = []
    for b in s.encode():
        c = chr(b)
        if c in '"\\':
            out.append("\\" + c)
        elif 32 <= b < 127 and c != "?":
            out.append(c)
        else:
            out.append("\\%03o" % b)     # octal: control chars, '?' (trigraphs), UTF-8 bytes
    return '"' + "".join(out) + '"'


def app_paths(name):
    app = APPS / f"{name}.app"
    return app, app / "Contents" / "MacOS" / name, app / "Contents" / "Resources" / CONFIG_NAME


def app_info(exe):
    """For a launcher path inside a .app: its name, bundle id, who built it and what it runs."""
    if ".app/Contents/MacOS/" not in exe:
        return None
    app = Path(exe.split(".app/Contents/MacOS/")[0] + ".app")
    info = {"app": app, "exe": exe, "name": app.stem, "bundle_id": "?", "kind": "unknown", "argv": None}
    try:
        ip = load_plist(app / "Contents" / "Info.plist")
        info["name"] = ip.get("CFBundleName", app.stem)
        info["bundle_id"] = ip.get("CFBundleIdentifier", "?")
    except Exception:
        pass
    ours, pa = app / "Contents/Resources" / CONFIG_NAME, app / "Contents/Resources/pa-app.json"
    try:
        if ours.exists():
            info.update(kind="mac-apps", argv=json.loads(ours.read_text())["argv"])
        elif pa.exists():
            c = json.loads(pa.read_text())
            info.update(kind="pa-app", argv=[c["python"], c["script"], *c.get("args", [])])
    except Exception:
        pass
    return info


def sign(app, bundle_id):
    r = None
    if SIGN_ID != "-":
        try:
            r = subprocess.run(["codesign", "--force", "--deep", "--sign", SIGN_ID, "--identifier", bundle_id, str(app)],
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            r = subprocess.CompletedProcess([], 1, "", "timed out (Keychain prompt waiting?)")
        if r.returncode:
            print(f"  signing with {SIGN_ID!r} failed ({r.stderr.strip()[:120]}); signing ad-hoc", file=sys.stderr)
    if r is None or r.returncode:
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", "--identifier", bundle_id, str(app)],
                       check=True, capture_output=True)


def build_app(name, argv, bundle_id=None, path_env=None, force=False):
    """Make <APPS>/<name>.app whose launcher runs argv. Reuses an identical existing build (a rebuild
    changes an ad-hoc signature, and macOS then forgets the app's privacy grants). Returns (exe, rebuilt)."""
    if not argv:
        die("nothing to run")
    argv = list(argv)
    if not os.path.isabs(argv[0]):
        found = which(argv[0], path_env)
        if not found:
            die(f"can't find {argv[0]!r} on PATH")
        argv[0] = found
    if not os.access(argv[0], os.X_OK):
        die(f"{argv[0]} is not executable")
    bundle_id = bundle_id or f"{PREFIX}.{slug(name)}"
    app, exe, cfg_path = app_paths(name)
    cfg = {"tool": "agent-tools/mac-apps", "name": name, "bundle_id": bundle_id, "argv": argv}
    if app.exists():
        old = None
        try:
            old = json.loads(cfg_path.read_text())
        except Exception:
            pass
        if old is None and not force:
            die(f"{tilde(app)} exists and wasn't built by mac-apps; move it away (or pass --force)")
        if old == cfg and exe.exists() and subprocess.run(["codesign", "--verify", str(app)],
                                                          capture_output=True).returncode == 0:
            return str(exe), False
        shutil.rmtree(app)
    cc = have_cc()
    if not cc:
        die("needs the Xcode Command Line Tools for `cc`: run `xcode-select --install`, then retry")
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Resources").mkdir(parents=True)
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "launcher.c"
        src.write_text(LAUNCHER_C.replace("@ARGV@", "".join(cstr(a) + ", " for a in argv)))
        r = subprocess.run([cc, "-O2", "-Wall", "-o", str(exe), str(src)], capture_output=True, text=True)
        if r.returncode:
            shutil.rmtree(app, ignore_errors=True)
            die(f"cc failed:\n{r.stderr}")
    with open(app / "Contents/Info.plist", "wb") as f:
        plistlib.dump({"CFBundleName": name, "CFBundleDisplayName": name, "CFBundleIdentifier": bundle_id,
                       "CFBundleExecutable": name, "CFBundlePackageType": "APPL", "CFBundleVersion": "1",
                       "CFBundleShortVersionString": "1.0", "LSUIElement": True, "LSBackgroundOnly": True}, f)
    cfg_path.write_text(json.dumps(cfg, indent=1) + "\n")
    os.chmod(cfg_path, 0o600)          # argv may carry a secret inline (it's local; plists already do)
    sign(app, bundle_id)
    subprocess.run(["xattr", "-cr", str(app)], capture_output=True)
    return str(exe), True


def probe(exe, paths, timeout=20):
    """Run the launcher in --probe mode from launchd (so macOS attributes the access to the app, not
    to this terminal). Returns (all_ok, output). Only for apps built by mac-apps: other launchers
    (e.g. pa-app's) would pass --probe through to the program they run."""
    info = app_info(exe)
    if not info or info["kind"] != "mac-apps":
        return False, "not a mac-apps launcher; not probing"
    label = f"local.mac-apps.probe.{slug(info['name'])}.{os.getpid()}"
    STATE.mkdir(parents=True, exist_ok=True)
    out = STATE / f"{label}.out"
    plist = STATE / f"{label}.plist"
    out.unlink(missing_ok=True)
    with open(plist, "wb") as f:
        plistlib.dump({"Label": label, "ProgramArguments": [exe, "--probe", *paths], "RunAtLoad": True,
                       "StandardOutPath": str(out), "StandardErrorPath": str(out)}, f)
    bootout(label, wait=2)
    ok, err = bootstrap(plist)
    if not ok:
        plist.unlink(missing_ok=True)
        return False, f"launchctl bootstrap failed: {err}"
    code = None
    for _ in range(timeout * 2):
        st = job_state(label) or {}
        lec = st.get("last exit code")
        if st.get("state") != "running" and lec and "never" not in lec:
            code = lec
            break
        time.sleep(0.5)
    bootout(label, wait=2)
    plist.unlink(missing_ok=True)
    text = out.read_text().strip() if out.exists() else ""
    out.unlink(missing_ok=True)
    if code is None:
        return False, (text + "\n" if text else "") + f"probe still running after {timeout}s (a privacy prompt waiting?)"
    return code.split()[0] == "0", text


def fda_paths():
    paths = [TCC_DB]
    if (HOME / "Library/Mail").is_dir():
        paths.append(str(HOME / "Library/Mail"))
    return paths


# ---------------------------------------------------------------------------------- detection
def effective(job):
    """(launchd argv, app info if it runs a named app, the argv that ultimately runs)."""
    a = argv_of(job)
    info = app_info(a[0]) if a else None
    inner = (info["argv"] or a) if info else a
    return a, info, inner


def classify(label, inner):
    j = " ".join(inner)
    if "probe" in label:
        return None
    if "stuck_watch" in j or "stuck-watch" in label:
        return "stuck-watch"
    if "cptr-watchdog" in j or ("watchdog" in label and "cptr" in label):
        return "watchdog"
    if label in ("com.cptr.run", "com.sandesh.cptr") or any(os.path.basename(x) == "cptr" for x in inner) \
            or re.search(r"(^|[\s/])cptr(\s|$)", j):
        if "cptr-input" in j or "agent-desktop" in label:
            return None
        return "cptr"
    return None


def detect():
    found = {"cptr": [], "watchdog": [], "stuck-watch": []}
    for p in sorted(AGENTS.glob("*.plist")):
        try:
            job = load_plist(p)
        except Exception:
            continue
        label = job.get("Label") or p.stem
        a, info, inner = effective(job)
        kind = classify(label, inner)
        if kind:
            found[kind].append({"label": label, "plist": p, "job": job, "argv": a, "app": info, "inner": inner})
    return found


def pick(found, kind):
    jobs = found[kind]
    if kind == "cptr" and os.environ.get("MAC_APPS_CPTR_LABEL"):
        jobs = [j for j in jobs if j["label"] == os.environ["MAC_APPS_CPTR_LABEL"]]
    if len(jobs) > 1:
        die(f"more than one {kind} LaunchAgent ({', '.join(j['label'] for j in jobs)}); "
            f"set MAC_APPS_CPTR_LABEL=<label>" if kind == "cptr" else f"more than one {kind} job")
    return jobs[0] if jobs else None


def cptr_port(j):
    """(port, how): the port it listens on now, else --port in its command, else CPTR_PORT, else 8000."""
    if os.environ.get("MAC_APPS_CPTR_PORT"):
        return int(os.environ["MAC_APPS_CPTR_PORT"]), "MAC_APPS_CPTR_PORT"
    st = job_state(j["label"]) or {}
    live = listening_ports(st.get("pid"))
    m = re.search(r"--port[= ]+(\d+)", " ".join(j["inner"]))
    env = (j["job"].get("EnvironmentVariables") or {}).get("CPTR_PORT")
    stated = (int(m.group(1)), "--port in its command") if m else (int(env), "CPTR_PORT") if env else None
    if stated and (not live or stated[0] in live):
        return stated[0], stated[1] + (" (listening)" if live else "")
    if live:
        return live[0], "listening now"
    return 8000, "default (not running, no --port)"


# ------------------------------------------------------------------------------------- ntfy
def ntfy_config():
    """Same discovery as Stuck Watch: env, ~/.config/stuck-watch/config, then an installed
    agent-tools mcp-tools LaunchAgent's environment."""
    cfg = {}
    try:
        for line in open(HOME / ".config/stuck-watch/config"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    cfg.update({k: v for k, v in os.environ.items() if k.startswith("NTFY_")})
    mcp = {}
    for name in ("com.sandesh.mcp-tools.plist", "com.sandesh.pa.agent-tools-mcp.plist"):
        try:
            mcp = load_plist(AGENTS / name).get("EnvironmentVariables", {}) or {}
        except Exception:
            continue
        if mcp:
            break
    if not cfg.get("NTFY_URL"):
        cfg.update({k: mcp.get(k) for k in ("NTFY_URL", "NTFY_TOPIC", "NTFY_TOKEN") if mcp.get(k)})
    host = mcp.get("COMPUTER_LABEL") or subprocess.run(["scutil", "--get", "ComputerName"],
                                                       capture_output=True, text=True).stdout.strip()
    return cfg.get("NTFY_URL"), cfg.get("NTFY_TOPIC") or "aibo", cfg.get("NTFY_TOKEN"), host or "Mac"


def notify(message, priority=3):
    url, topic, token, host = ntfy_config()
    if not url:
        return
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps({"topic": topic, "title": f"mac-apps on {host}", "message": message,
                       "priority": priority, "tags": ["package"]}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url.rstrip("/") + "/", data=body, method="POST",
                                                      headers=headers), timeout=10).read()
    except Exception as e:
        log(f"ntfy failed: {e}", quiet=True)


# ---------------------------------------------------------------------------- privacy entries
SERVICES = {
    "kTCCServiceSystemPolicyAllFiles": "Full Disk Access",
    "kTCCServiceSystemPolicyDocumentsFolder": "Files & Folders (Documents)",
    "kTCCServiceSystemPolicyDesktopFolder": "Files & Folders (Desktop)",
    "kTCCServiceSystemPolicyDownloadsFolder": "Files & Folders (Downloads)",
    "kTCCServiceSystemPolicyNetworkVolumes": "Files & Folders (Network Volumes)",
    "kTCCServiceSystemPolicyRemovableVolumes": "Files & Folders (Removable Volumes)",
    "kTCCServiceFileProviderDomain": "Files & Folders (iCloud / file providers)",
    "kTCCServiceSystemPolicyAppData": "App Management / other apps' data",
    "kTCCServiceSystemPolicyAppBundles": "App Management",
    "kTCCServiceAppleEvents": "Automation",
    "kTCCServiceAccessibility": "Accessibility",
    "kTCCServiceScreenCapture": "Screen & System Audio Recording",
    "kTCCServicePhotos": "Photos",
    "kTCCServiceMediaLibrary": "Media & Apple Music",
}
INTERP = re.compile(r"(^|/)(python[0-9.]*|bash|zsh|sh|node|ruby|perl)$")


def tcc_rows():
    """[(service, client, allowed)] from the system and user TCC databases, or None if this shell
    can't read them (reading them needs Full Disk Access itself)."""
    if not shutil.which("sqlite3"):
        return None
    rows, readable = [], False
    for db in (TCC_DB, USER_TCC_DB):
        r = subprocess.run(["sqlite3", "-separator", "\t", f"file:{db}?mode=ro",
                            "select service, client, client_type, auth_value from access"],
                           capture_output=True, text=True)
        if r.returncode:
            continue
        readable = True
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 4:
                rows.append((parts[0], parts[1], parts[2] == "1", parts[3] == "2"))
    return rows if readable else None


def interpreters(argv, path_env=None):
    """Interpreter binaries an argv ends up running (resolving shebangs and `exec cmd` in zsh -c)."""
    out, cands = set(), [argv[0]] if argv else []
    if len(argv) >= 3 and argv[1] in ("-c", "-lc"):
        cands += re.findall(r"(?:^|[;&|]\s*|\bexec\s+)([~/\w.-]+)", argv[2])
    for c in cands:
        c = os.path.expanduser(c)
        p = c if os.path.isabs(c) else which(c, path_env)
        for _ in range(3):
            if not p or not os.path.isfile(p):
                break
            rp = os.path.realpath(p)
            if INTERP.search(rp) or INTERP.search(p):
                out.add(rp)
                break
            try:
                with open(rp, "rb") as f:
                    head = f.readline(300)
            except OSError:
                break
            if not head.startswith(b"#!"):
                break
            words = head[2:].decode(errors="replace").split()
            if not words:
                break
            p = which(words[1]) if os.path.basename(words[0]) == "env" and len(words) > 1 else words[0]
    return out


def stale_report(found):
    print("\n== privacy entries left behind ==")
    wrapped = [j for js in found.values() for j in js if j["app"]]
    used = set()
    for j in wrapped:
        used |= interpreters(j["inner"], (j["job"].get("EnvironmentVariables") or {}).get("PATH"))
    # interpreters that unwrapped LaunchAgents still run directly (their grants are still needed)
    still = {}
    for p in sorted(AGENTS.glob("*.plist")):
        try:
            job = load_plist(p)
        except Exception:
            continue
        a, info, _ = effective(job)
        if a and not info:
            for i in interpreters(a, (job.get("EnvironmentVariables") or {}).get("PATH")):
                still.setdefault(i, []).append(job.get("Label") or p.stem)
    rows = tcc_rows()
    if rows is None:
        print("  can't read the TCC databases from this shell (that needs Full Disk Access).")
        if used:
            print("  Look in System Settings > Privacy & Security for entries named:")
            for i in sorted(used):
                print(f"    {os.path.basename(i)}   ({tilde(i)})" + (f"  - still used by {', '.join(still[i])}" if i in still else ""))
        print_remove_steps()
        return
    hits = [(s, c, ok) for s, c, is_path, ok in rows if is_path and INTERP.search(c) and ok]
    if not hits:
        print("  none: no interpreter (python/bash/zsh/...) holds a privacy grant")
        return
    by = {}
    for s, c, ok in hits:
        by.setdefault(c, []).append(SERVICES.get(s, s.replace("kTCCService", "")))
    for c in sorted(by):
        rc = os.path.realpath(c)
        users = still.get(rc) or still.get(c)
        tag = f"KEEP - still used by {', '.join(users)}" if users else \
              ("stale - its job now runs as a named app" if rc in used else "probably stale - no LaunchAgent runs it")
        print(f"  {os.path.basename(c):14} {tilde(c)}\n      {', '.join(sorted(set(by[c])))}\n      -> {tag}")
    print("  (Interactive tools you run in a terminal inherit the terminal's grants, not these; an entry")
    print("   can also belong to a cron job or script outside launchd. Check before removing.)")
    print_remove_steps()


def print_remove_steps():
    print("  To remove one: System Settings > Privacy & Security > <the section listed> > select the")
    print("  entry > click - (minus) below the list > confirm with your password / Touch ID.")


# ------------------------------------------------------------------------------------- check
def ok(s):
    print(f"  \033[32m✓\033[0m {s}")


def note(s):
    print(f"  \033[33m•\033[0m {s}")


def fda_granted_to(bundle_id, rows):
    if rows is None:
        return None
    for s, c, is_path, allowed in rows:
        if s == "kTCCServiceSystemPolicyAllFiles" and c == bundle_id:
            return allowed
    return False


def describe(kind, j, rows):
    print(f"  LaunchAgent {j['label']}  ({tilde(j['plist'])})")
    job = j["job"]
    st = job_state(j["label"])
    if st is None:
        note("not loaded")
    else:
        s = st["state"] + (f", pid {st['pid']}" if st.get("pid") else "") + \
            (f", last exit {st['last exit code']}" if st.get("last exit code") else "")
        print(f"    state:   {s}")
    print(f"    runs:    {' '.join(tilde(x) for x in j['argv'])[:200]}")
    if j["app"]:
        info = j["app"]
        print(f"    inner:   {' '.join(tilde(x) for x in j['inner'])[:200]}")
    env = job.get("EnvironmentVariables") or {}
    print(f"    env:     {', '.join(sorted(env)) or '-'}   workdir: {tilde(job.get('WorkingDirectory', '-'))}")
    logs = {job.get("StandardOutPath"), job.get("StandardErrorPath")} - {None}
    sched = "KeepAlive" if job.get("KeepAlive") else f"every {job['StartInterval']}s" if job.get("StartInterval") else "at load"
    print(f"    logs:    {', '.join(tilde(x) for x in sorted(logs)) or '-'}   schedule: {sched}")
    if kind == "cptr":
        port, how = cptr_port(j)
        h = healthy(port)
        print(f"    port:    {port} ({how}); health: {'answering' if h else 'NOT answering'}")
    if j["app"]:
        info = j["app"]
        by = {"pa-app": "Personal Agent's pa-app", "mac-apps": "mac-apps"}.get(info["kind"], "something else")
        ok(f"already named: \"{info['name']}\" ({info['bundle_id']}, built by {by}, {tilde(info['app'])})")
        if kind == "cptr":
            g = fda_granted_to(info["bundle_id"], rows)
            if info["kind"] == "mac-apps":
                good, text = probe(info["exe"], fda_paths())
                (ok if good else note)("Full Disk Access: " + ("works (probe as the app read " + TCC_DB + ")" if good
                                                                else "probe FAILED: " + text.replace("\n", "; ")))
            elif g is not None:
                (ok if g else note)(f"Full Disk Access for {info['bundle_id']}: {'granted' if g else 'NOT granted'} (TCC.db)")
            else:
                note("Full Disk Access: can't tell from this shell (no access to TCC.db)")
        print("    would change: nothing")
    else:
        name = NAMES[kind]
        app, exe, _ = app_paths(name)
        print(f"    would change: build {tilde(app)} (bundle {PREFIX}.{slug(name)}) running the command above,")
        if kind == "cptr":
            print("      have you grant it Full Disk Access (checked by a probe as the app), back up the plist,")
            print(f"      point ProgramArguments at {tilde(exe)} (label/env/workdir/logs/KeepAlive kept),")
            print("      restart detached and roll back unless health + a protected-path probe pass")
        else:
            print(f"      back up the plist, point ProgramArguments at the launcher, reload (no Full Disk Access needed)")


def check(found=None):
    found = found or detect()
    print("mac-apps --check (changes nothing)")
    print(f"  apps folder: {tilde(APPS)} ({'exists' if APPS.is_dir() else 'will be created'}); bundle prefix {PREFIX}; "
          f"signing: {'ad-hoc' if SIGN_ID == '-' else SIGN_ID}")
    cc = have_cc()
    (ok if cc else note)(f"cc: {cc}" if cc else "no Command Line Tools: `xcode-select --install` (needed to build)")
    rows = tcc_rows()
    for kind, title in (("cptr", "cptr"), ("watchdog", "cptr watchdog"), ("stuck-watch", "Stuck Watch")):
        print(f"\n== {title} ==")
        if not found[kind]:
            note("no LaunchAgent found" + (" (install it with agent-tools/" + kind + "/install.sh)" if kind != "cptr" else ""))
            continue
        for j in found[kind]:
            describe(kind, j, rows)
    if found["watchdog"] and found["cptr"]:
        c = found["cptr"][0]
        w = found["watchdog"][0]
        wenv = w["job"].get("EnvironmentVariables") or {}
        wl, wp = wenv.get("CPTR_WATCHDOG_LABEL", "com.cptr.run"), wenv.get("CPTR_WATCHDOG_PORT", "8000")
        cp = cptr_port(c)[0]
        if wl != c["label"] or str(wp) != str(cp):
            note(f"the watchdog targets {wl} :{wp} but cptr is {c['label']} :{cp}; set CPTR_WATCHDOG_LABEL/"
                 f"CPTR_WATCHDOG_PORT in the watchdog's plist EnvironmentVariables")
    backups = sorted(BACKUPS.glob("*.plist")) if BACKUPS.is_dir() else []
    if backups:
        print(f"\n  rollback available: {len(backups)} backup(s) in {tilde(BACKUPS)}")
    stale_report(found)
    return 0


# -------------------------------------------------------------------------------- wrapping
def backup(j):
    BACKUPS.mkdir(parents=True, exist_ok=True)
    dst = BACKUPS / f"{j['label']}.{datetime.now().strftime('%Y%m%d-%H%M%S')}.plist"
    shutil.copy2(j["plist"], dst)
    return dst


def wrapped_job(j, exe):
    job = dict(j["job"])
    job.pop("Program", None)
    job["ProgramArguments"] = [exe]
    return job


def wrap_simple(kind, found):
    """Watchdog / Stuck Watch: no Full Disk Access needed; wrap, reload, verify, else roll back."""
    j = pick(found, kind)
    name = NAMES[kind]
    print(f"\n== {name} ==")
    if not j:
        note(f"no LaunchAgent found; install it first (agent-tools/{kind if kind != 'watchdog' else 'cptr-watchdog'}/install.sh)")
        return 0
    if j["app"]:
        ok(f"already named \"{j['app']['name']}\" ({j['app']['bundle_id']}); nothing to do")
        return 0
    env = j["job"].get("EnvironmentVariables") or {}
    exe, rebuilt = build_app(name, j["argv"], path_env=env.get("PATH"))
    ok(f"{'built' if rebuilt else 'reusing'} {tilde(exe.split('/Contents/')[0])}")
    bk = backup(j)
    write_plist(j["plist"], wrapped_job(j, exe))
    bootout(j["label"])
    good, err = bootstrap(j["plist"])
    why = err
    if good:
        if j["job"].get("KeepAlive"):
            time.sleep(6)
            st = job_state(j["label"]) or {}
            good = st.get("state") == "running" and st.get("program") == exe
            why = f"state {st.get('state')}, last exit {st.get('last exit code')}"
        else:
            launchctl("kickstart", f"{DOMAIN}/{j['label']}")
            good, why = False, "no run finished within 60s"
            for _ in range(60):
                time.sleep(1)
                st = job_state(j["label"]) or {}
                lec = st.get("last exit code") or ""
                if st.get("state") != "running" and lec and "never" not in lec:
                    good, why = lec.split()[0] == "0", f"last exit {lec}"
                    break
    if good:
        log(f"RESULT {kind}: OK - {j['label']} now runs as \"{name}\" ({tilde(exe)}); backup {tilde(bk)}")
        print(f"  If macOS asks \"{name} would like to access files in your Documents folder\" (or similar)")
        print("  the first time, click Allow: it's the new app identity asking once.")
        notify(f"{name}: now runs as a named app ({j['label']})")
        return 0
    log(f"RESULT {kind}: FAILED ({why}) - restoring {tilde(bk)}")
    shutil.copy2(bk, j["plist"])
    bootout(j["label"])
    bootstrap(j["plist"])
    notify(f"{name}: switch to a named app failed ({why}); restored the previous LaunchAgent", 4)
    return 1


def wait_fda(exe):
    good, text = probe(exe, fda_paths())
    if good:
        ok("cptr.app already has Full Disk Access")
        return True
    app = exe.split("/Contents/")[0]
    subprocess.run(["open", FDA_URL])
    print(f"""
  cptr.app needs Full Disk Access BEFORE cptr is switched to it (otherwise cptr freezes on the first
  protected file). System Settings is opening at Privacy & Security > Full Disk Access:

    1. click + below the list (enter your password / Touch ID if asked)
    2. press Cmd+Shift+G and paste:  {app}
    3. press Return, then click Open
    4. make sure the new "cptr" entry's switch is ON

  Waiting for it (checking every 5 s as the app, up to 10 minutes; Ctrl-C to stop - nothing has
  been changed yet)...""", flush=True)
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(5)
        good, text = probe(exe, fda_paths())
        if good:
            ok("Full Disk Access confirmed (the app read " + TCC_DB + ")")
            return True
    print(f"  last probe: {text}")
    return False


def cmd_cptr(found):
    j = pick(found, "cptr")
    print("\n== cptr ==")
    if not j:
        note("no LaunchAgent runs cptr; nothing to do")
        return 0
    if j["app"]:
        ok(f"already named \"{j['app']['name']}\" ({j['app']['bundle_id']}); nothing to do")
        return 0
    env = j["job"].get("EnvironmentVariables") or {}
    port, how = cptr_port(j)
    print(f"  {j['label']}: port {port} ({how})")
    exe, rebuilt = build_app("cptr", j["argv"], path_env=env.get("PATH"))
    ok(f"{'built' if rebuilt else 'reusing'} {tilde(exe.split('/Contents/')[0])}")
    if not wait_fda(exe):
        log("RESULT cptr: not switched - Full Disk Access for cptr.app was not granted within 10 minutes")
        return 1
    bk = backup(j)
    w = pick(found, "watchdog")
    state = {"label": j["label"], "plist": str(j["plist"]), "backup": str(bk), "exe": exe, "port": port,
             "job": wrapped_job(j, exe), "watchdog": ({"label": w["label"], "plist": str(w["plist"])} if w else None)}
    return detach("apply", state)


def detach(mode, state):
    STATE.mkdir(parents=True, exist_ok=True)
    sf = STATE / f"switch-{state['label']}.json"
    sf.write_text(json.dumps(state, default=str))
    os.chmod(sf, 0o600)
    log(f"cptr: switching {state['label']} ({mode}) in the background; backup {tilde(state['backup'])}")
    print("  cptr restarts now. If you're running this inside cptr, your session drops here; the switch")
    print(f"  finishes on its own and writes the result to {tilde(LOG)} (and ntfy, if configured).")
    start = LOG.stat().st_size if LOG.exists() else 0
    with open(LOG, "a") as lf:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "_switch", mode, str(sf)],
                         stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                         start_new_session=True, close_fds=True, cwd=str(HOME))
    deadline = time.time() + 240
    while time.time() < deadline:
        time.sleep(2)
        with open(LOG) as f:
            f.seek(start)
            for line in f.read().splitlines():
                if " RESULT cptr" in line:
                    print("  " + line.split(" ", 1)[1])
                    return 0 if "RESULT cptr: OK" in line else 1
    print(f"  no result yet; see {tilde(LOG)}")
    return 1


def restart(label, plist, port):
    """bootout, make sure nothing still holds the port (a frozen copy did, in the incident), bootstrap."""
    bootout(label)
    for _ in range(20):
        if not port_pids(port):
            break
        time.sleep(1)
    for sig in ("-TERM", "-KILL"):
        pids = [p for p in port_pids(port)
                if "cptr" in subprocess.run(["ps", "-o", "command=", "-p", p], capture_output=True, text=True).stdout]
        if not pids:
            break
        log(f"  port {port} still held by stray cptr pid(s) {' '.join(pids)}; kill {sig}", quiet=True)
        subprocess.run(["kill", sig, *pids], capture_output=True)
        time.sleep(5)
    if port_pids(port):
        log(f"  port {port} is still in use by something else", quiet=True)
    return bootstrap(plist)


def gate(label, exe, port, timeout=60):
    """Healthy = launchd runs the launcher, cptr answers on its port, the app identity can read a
    Full-Disk-Access-only path, and cptr keeps answering (the incident's copy froze after starting)."""
    deadline = time.time() + timeout
    while not healthy(port):
        if time.time() > deadline:
            return False, f"no HTTP answer on :{port} within {timeout}s"
        time.sleep(2)
    st = job_state(label) or {}
    if st.get("program") != exe or st.get("state") != "running":
        return False, f"launchd shows program {st.get('program')}, state {st.get('state')}"
    pid = st.get("pid")
    good, text = probe(exe, fda_paths())
    if not good:
        return False, "protected-path probe as cptr.app failed: " + text.replace("\n", "; ")
    for _ in range(4):
        time.sleep(4)
        if not healthy(port):
            return False, f"cptr stopped answering on :{port} after starting"
    st = job_state(label) or {}
    if st.get("pid") != pid:
        return False, f"cptr restarted during the check (pid {pid} -> {st.get('pid')})"
    return True, f"answering on :{port}, Full Disk Access probe ok, stable"


def _switch(mode, statefile):
    """Detached part: survives the restart of the cptr that launched it."""
    s = json.loads(Path(statefile).read_text())
    label, plist, port = s["label"], Path(s["plist"]), int(s["port"])
    w = s.get("watchdog")
    log(f"switch {mode} {label}: start")
    paused = False
    if w and job_state(w["label"]) is not None:      # no watchdog restarts colliding mid-switch
        paused = bootout(w["label"])
        log(f"  paused watchdog {w['label']}", quiet=True)
    try:
        if mode == "apply":
            write_plist(plist, s["job"])
            good, err = restart(label, plist, port)
            ok_, why = gate(label, s["exe"], port) if good else (False, f"bootstrap failed: {err}")
            if ok_:
                log(f"RESULT cptr: OK - {label} now runs as cptr.app ({why}); backup {tilde(s['backup'])}")
                notify(f"cptr now runs as cptr.app ({why})")
                return 0
            log(f"  switch failed: {why}; restoring {tilde(s['backup'])}")
        else:
            why = "rollback requested"
        shutil.copy2(s["backup"], plist)
        restart(label, plist, port)
        back = False
        for _ in range(30):
            time.sleep(2)
            if healthy(port):
                back = True
                break
        if mode == "apply":
            msg = f"switch to cptr.app FAILED ({why}); previous LaunchAgent restored, " + \
                  ("cptr healthy again" if back else f"but cptr is NOT answering on :{port} - needs a human")
        else:
            msg = f"rolled back to {tilde(s['backup'])}; " + ("cptr healthy" if back else f"cptr NOT answering on :{port}")
        log(f"RESULT cptr: {'OK - ' if mode != 'apply' and back else 'FAILED - '}{msg}")
        notify("cptr: " + msg, 5 if not back else 4)
        return 1
    finally:
        if paused:
            bootstrap(w["plist"])
            log(f"  resumed watchdog {w['label']}", quiet=True)


def rollback(found):
    """Restore the newest backup of every LaunchAgent that currently runs a mac-apps launcher."""
    if not BACKUPS.is_dir():
        print("nothing to roll back (no backups)")
        return 0
    rc = 0
    for kind in ("watchdog", "stuck-watch", "cptr"):
        for j in found[kind]:
            if not j["app"] or j["app"]["kind"] != "mac-apps":
                if j["app"]:
                    note(f"{j['label']} runs \"{j['app']['name']}\" built by {j['app']['kind']}, not mac-apps; leaving it")
                continue
            bks = sorted(BACKUPS.glob(f"{j['label']}.*.plist"))
            if not bks:
                note(f"{j['label']}: no backup in {tilde(BACKUPS)}")
                continue
            bk = bks[-1]
            if kind == "cptr":
                w = pick(found, "watchdog")
                rc |= detach("restore", {"label": j["label"], "plist": str(j["plist"]), "backup": str(bk),
                                         "exe": j["argv"][0], "port": cptr_port(j)[0],
                                         "watchdog": {"label": w["label"], "plist": str(w["plist"])} if w else None})
            else:
                shutil.copy2(bk, j["plist"])
                bootout(j["label"])
                good, err = bootstrap(j["plist"])
                log(f"RESULT {kind}: {'OK - restored' if good else 'FAILED - ' + err} {tilde(bk)}")
                rc |= 0 if good else 1
    print(f"  The apps stay in {tilde(APPS)} (delete them by hand if you want them gone).")
    return rc


# ---------------------------------------------------------------------------------------- main
def main(argv):
    if not argv or argv[0] in ("--check", "check"):
        return check()
    cmd = argv[0]
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd == "_switch":
        return _switch(argv[1], argv[2])
    if cmd == "build":
        if len(argv) < 2 or "--" not in argv:
            die('usage: build "<Name>" [--bundle-id ID] [--force] -- <argv...>')
        i = argv.index("--")
        opts, run = argv[2:i], argv[i + 1:]
        bid = opts[opts.index("--bundle-id") + 1] if "--bundle-id" in opts else None
        exe, rebuilt = build_app(argv[1], run, bid, force="--force" in opts)
        print(f"{'built' if rebuilt else 'unchanged'}: {exe}")
        return 0
    if cmd == "probe":
        if len(argv) < 3:
            die('usage: probe "<Name>" <path...>')
        good, text = probe(str(app_paths(argv[1])[1]), argv[2:], timeout=120)
        print(text)
        return 0 if good else 1
    found = detect()
    if cmd == "--rollback":
        return rollback(found)
    if cmd in ("--watchdog", "--stuck-watch"):
        rc = wrap_simple(cmd[2:], found)
        stale_report(detect())
        return rc
    if cmd == "--cptr":
        return cmd_cptr(found)
    if cmd == "--all":
        rc = wrap_simple("stuck-watch", found) | wrap_simple("watchdog", found)
        found = detect()                       # watchdog may be wrapped now (cptr's switch pauses it)
        rc |= cmd_cptr(found)
        stale_report(detect())
        return rc
    die(f"unknown option {cmd!r} (see --help)")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
