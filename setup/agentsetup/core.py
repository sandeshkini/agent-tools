"""The pieces every component shares: plans, the run context, logging with secret redaction, and
snapshot-based rollback of the files a component's installer writes."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

OK, CHANGE, GAP, SKIP, ERROR = "ok", "change", "gap", "skip", "error"
LABELS = {OK: "in place", CHANGE: "would change", GAP: "needs a person", SKIP: "skipped", ERROR: "error"}


class Plan:
    """What a component found and what applying it would do.

    status   OK (in place), CHANGE (setup can do it), GAP (missing, and needs a person or a decision),
             SKIP (not applicable here / disabled), ERROR (detection failed)
    summary  one line
    actions  what apply would do, in order
    human    steps only a person can do (passwords, privacy grants)
    notes    anything else worth knowing
    data     what apply() needs to know about the plan (component-specific)
    """

    def __init__(self, status, summary, actions=None, human=None, notes=None, data=None):
        self.status, self.summary = status, summary
        self.actions, self.human, self.notes = list(actions or []), list(human or []), list(notes or [])
        self.data = dict(data or {})           # for the component's own apply()

    def __repr__(self):
        return f"Plan({self.status!r}, {self.summary!r})"


class Component:
    """One thing setup manages. Subclasses implement detect() (read the machine, return plain data) and
    plan(facts) (pure: facts + profile settings -> Plan, so it can be tested with fake facts). apply()
    does the change, verify() re-detects, rollback() undoes the last apply.

    owned_files(): files apply may create or overwrite. Before apply, setup snapshots them; the default
    rollback() restores that snapshot (deleting files that didn't exist, reloading LaunchAgents and
    systemd units)."""
    name = ""
    description = ""
    platforms = ("darwin", "linux")
    order = 50
    restarts_cptr = False          # apply may restart cptr: needs a person at the Mac (or --yes)

    def __init__(self, ctx, cfg=None):
        self.ctx = ctx
        self.cfg = dict(cfg or {})

    def not_applicable(self):
        """None if this component applies on this OS, else the reason it doesn't."""
        if self.ctx.os not in self.platforms:
            return f"not applicable on {self.ctx.os_label}"
        return None

    def detect(self):
        return {}

    def plan(self, facts):
        raise NotImplementedError

    def apply(self, plan):
        return False

    def verify(self):
        p = self.plan(self.detect())
        return p.status == OK, p.summary

    def owned_files(self):
        return []

    def rollback(self):
        return restore_snapshot(self.ctx, self.name)


# ------------------------------------------------------------------------------------ redaction
_SECRET_PATTERNS = [
    (re.compile(r"((?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|_KEY)[A-Z0-9_]*\s*[=:]\s*)(['\"]?)[^\s'\"]+", re.I), r"\1\2***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1***"),
    (re.compile(r"(://[^/\s:@]+:)[^@\s]+@"), r"\1***@"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|tk_[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{16,}|xox[abp]-[A-Za-z0-9-]{10,})"), "***"),
]


def redact(text, extra=()):
    for v in extra:
        if v and len(v) >= 8:
            text = text.replace(v, "***")
    for pat, rep in _SECRET_PATTERNS:
        text = pat.sub(rep, text)
    return text


# -------------------------------------------------------------------------------------- context
class Context:
    """Run-wide state: OS, paths, the profile, logging, and running commands with redacted output."""

    def __init__(self, repo, profile, os_name=None, home=None, assume_yes=False, interactive=None, verbose=False):
        self.repo = Path(repo)
        self.profile = profile or {}
        self.os = os_name or sys.platform.replace("linux2", "linux")
        self.os_label = {"darwin": "macOS", "linux": "Linux"}.get(self.os, self.os)
        self.home = Path(home or Path.home())
        self.assume_yes = assume_yes
        self.interactive = sys.stdin.isatty() and sys.stdout.isatty() if interactive is None else interactive
        self.verbose = verbose
        if self.os == "darwin":
            self.log_path = self.home / "Library/Logs/agent-setup.log"
        else:
            self.log_path = self.home / ".local/state/agent-setup/agent-setup.log"
        self.state_dir = self.home / ".local/state/agent-setup"
        self._secrets = None
        self.cache = {}

    def fresh(self):
        """Forget cached detection results (before re-detecting after a change)."""
        self.cache = {k: v for k, v in self.cache.items() if k == "mac_apps"}

    # profile helpers
    def section(self, name):
        v = self.profile.get(name)
        return v if isinstance(v, dict) else {}

    def expand(self, value):
        """~, {agent_tools} (this clone) and {profile_dir} (the profile's folder) in a profile path."""
        if not isinstance(value, str):
            return value
        pdir = str(Path(self.profile.get("_path", ".")).resolve().parent)
        return os.path.expanduser(value.replace("{agent_tools}", str(self.repo)).replace("{profile_dir}", pdir))

    # secrets: never printed, only handed to installers that need them
    def secrets(self):
        if self._secrets is None:
            vals = {}
            f = self.section("secrets").get("env_file")
            if f:
                vals.update(read_env_file(self.expand(f)))
            vals.update({k: v for k, v in os.environ.items() if k in vals or k.endswith(("_TOKEN", "_KEY"))})
            self._secrets = vals
        return self._secrets

    def secret_values(self):
        return [v for v in self.secrets().values() if isinstance(v, str)]

    # logging
    def log(self, msg):
        line = f"{datetime.now().isoformat(timespec='seconds')} {redact(msg, self.secret_values())}"
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def say(self, msg="", log=True):
        msg = redact(msg, self.secret_values())
        print(msg, flush=True)
        if log and msg.strip():
            self.log(msg.strip())

    # commands
    def capture(self, cmd, timeout=20, env=None, cwd=None):
        """Run quietly; returns CompletedProcess (returncode 127 if missing, 124 on timeout)."""
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                                  cwd=cwd or str(self.home))
        except FileNotFoundError:
            return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: not found")
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(cmd, 124, "", f"timed out after {timeout}s")

    def run(self, cmd, extra_env=None, indent="      "):
        """Run an installer: stream its output (redacted) to the terminal and the log. Returns exit code.
        stdin stays attached, so an installer that asks a person something can."""
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (extra_env or {}).items() if v is not None})
        shown = " ".join(str(c) for c in cmd).replace(str(self.home), "~")
        self.log(f"run: {shown}")
        try:
            p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, env=env, cwd=str(self.home), bufsize=1)
        except OSError as e:
            self.say(f"{indent}cannot run {shown}: {e}")
            return 127
        for line in p.stdout:
            self.say(indent + line.rstrip("\n"))
        rc = p.wait()
        self.log(f"exit {rc}: {shown}")
        return rc


def read_env_file(path):
    """KEY=VALUE lines (optional `export`, quotes); comments and anything else ignored."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
                if not m:
                    continue
                v = m.group(2).strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                else:
                    v = re.sub(r"\s+#.*$", "", v)
                out[m.group(1)] = v
    except OSError:
        pass
    return out


# ------------------------------------------------------------------------------------ snapshots
def _snap_root(ctx, name):
    return ctx.state_dir / "backups" / name


def take_snapshot(ctx, name, paths):
    """Copy each path that exists; remember the ones that didn't (rollback deletes those)."""
    paths = [Path(p) for p in paths]
    if not paths:
        return None
    d = _snap_root(ctx, name) / datetime.now().strftime("%Y%m%d-%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)                         # plists can hold tokens
    manifest = {"created": time.time(), "files": {}}
    for i, p in enumerate(paths):
        if p.is_file():
            dst = d / f"{i}-{p.name}"
            shutil.copy2(p, dst)
            manifest["files"][str(p)] = str(dst)
        else:
            manifest["files"][str(p)] = None
    (d / "manifest.json").write_text(json.dumps(manifest, indent=1))
    ctx.log(f"snapshot {name}: {d}")
    return d


def latest_snapshot(ctx, name):
    root = _snap_root(ctx, name)
    snaps = sorted(p for p in root.glob("*/manifest.json") if not p.parent.name.endswith(".rolled-back")) \
        if root.is_dir() else []
    return snaps[-1].parent if snaps else None


def _launch_label(path):
    try:
        import plistlib
        with open(path, "rb") as f:
            return plistlib.load(f).get("Label") or Path(path).stem
    except Exception:
        return Path(path).stem


def restore_snapshot(ctx, name):
    """Undo the last apply of `name`: put back the files it changed, delete the ones it created, and
    reload the LaunchAgents / systemd units among them. Returns True / False, or None if there's
    nothing to undo."""
    d = latest_snapshot(ctx, name)
    if not d:
        ctx.say(f"  {name}: nothing to roll back (setup hasn't changed it: no snapshot in "
                f"{str(_snap_root(ctx, name)).replace(str(ctx.home), '~')})")
        return None
    manifest = json.loads((d / "manifest.json").read_text())
    domain = f"gui/{os.getuid()}"
    agents_dir = str(ctx.home / "Library/LaunchAgents")
    units_dir = str(ctx.home / ".config/systemd/user")
    reload_agents, units, ok = [], [], True
    for path, backup in manifest["files"].items():
        is_agent = path.startswith(agents_dir) and path.endswith(".plist")
        is_unit = path.startswith(units_dir)
        if is_agent:
            label = _launch_label(path) if os.path.exists(path) else Path(path).stem
            ctx.capture(["launchctl", "bootout", f"{domain}/{label}"])
        if is_unit and backup is None and os.path.exists(path) and path.endswith((".service", ".timer")):
            ctx.capture(["systemctl", "--user", "disable", "--now", Path(path).name])
        try:
            if backup:
                shutil.copy2(backup, path)
                if is_agent:
                    reload_agents.append(path)
                ctx.say(f"  restored {path.replace(str(ctx.home), '~')}")
            elif os.path.lexists(path):
                os.unlink(path)
                ctx.say(f"  removed {path.replace(str(ctx.home), '~')} (didn't exist before)")
        except OSError as e:
            ok = False
            ctx.say(f"  could not restore {path}: {e}")
        if is_unit:
            units.append(path)
    for path in reload_agents:
        r = ctx.capture(["launchctl", "bootstrap", domain, path])
        if r.returncode:
            ok = False
            ctx.say(f"  launchctl bootstrap {Path(path).name} failed: {r.stderr.strip()}")
    if units:
        ctx.capture(["systemctl", "--user", "daemon-reload"])
        for u in units:
            if os.path.exists(u) and u.endswith(".timer"):
                ctx.capture(["systemctl", "--user", "enable", "--now", Path(u).name])
    done = d.with_name(d.name + ".rolled-back")
    os.rename(d, done)
    ctx.log(f"rollback {name}: {'ok' if ok else 'FAILED'} from {d}")
    return ok
