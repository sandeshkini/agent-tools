"""Read-only facts about this machine: identity, launchd / systemd jobs, HTTP checks, cptr.

LaunchAgent discovery reuses agent-tools/mac-apps (mac_apps.detect), so setup and mac-apps agree on
which job is cptr, the watchdog, Stuck Watch, the session sync and mcp-tools, and on whether it already
runs as a named app.
"""
import importlib.util
import json
import os
import platform
import re
import shutil
import socket
import urllib.error
import urllib.request
from pathlib import Path

# Environment variable names whose values are safe to show (everything else: names only).
SAFE_ENV = {"CPTR_WATCHDOG_LABEL", "CPTR_WATCHDOG_PORT", "CPTR_PORT", "PORT", "HOST", "COMPUTER_LABEL"}
EXTRA_PATH = ["/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", "~/.opencode/bin"]


# ------------------------------------------------------------------------------------- identity
def identity(ctx):
    """hostname, local_hostname, computer_name, model, os: what profile [match] tables compare against."""
    f = {"os": ctx.os, "hostname": socket.gethostname(), "local_hostname": "", "computer_name": "", "model": ""}
    if ctx.os == "darwin":
        f["computer_name"] = ctx.capture(["scutil", "--get", "ComputerName"]).stdout.strip()
        f["local_hostname"] = ctx.capture(["scutil", "--get", "LocalHostName"]).stdout.strip()
        f["model"] = ctx.capture(["sysctl", "-n", "hw.model"]).stdout.strip()
    else:
        f["computer_name"] = f["hostname"]
        f["local_hostname"] = f["hostname"].split(".")[0]
        for p in ("/sys/class/dmi/id/product_name", "/sys/firmware/devicetree/base/model"):
            try:
                f["model"] = Path(p).read_text().strip("\x00\n ")
                break
            except OSError:
                pass
    f["arch"] = platform.machine()
    return f


# ---------------------------------------------------------------------------------------- paths
def search_path():
    parts = os.environ.get("PATH", "").split(os.pathsep) + [os.path.expanduser(p) for p in EXTRA_PATH]
    return os.pathsep.join(dict.fromkeys(p for p in parts if p))


def which(cmd):
    return shutil.which(cmd, path=search_path())


def http_status(url, timeout=4):
    """HTTP status code, or None if nothing answers."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def cptr_healthy(port):
    """cptr answers: /health 200, or / with a page or a login wall (password-mode cptr)."""
    if http_status(f"http://127.0.0.1:{port}/health") == 200:
        return True
    c = http_status(f"http://127.0.0.1:{port}/")
    return c is not None and (200 <= c < 400 or c in (401, 403))


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def claude_mcp_servers(ctx):
    """Names of user-scope MCP servers in ~/.claude.json (values are never read out)."""
    d = read_json(ctx.home / ".claude.json") or {}
    return set((d.get("mcpServers") or {}).keys())


def opencode_config_text(ctx):
    for name in ("opencode.jsonc", "opencode.json"):
        p = ctx.home / ".config/opencode" / name
        if p.is_file():
            try:
                return p.read_text()
            except OSError:
                pass
    return None


# --------------------------------------------------------------------------------------- launchd
def mac_apps(ctx):
    """agent-tools/mac-apps/mac_apps.py, imported once."""
    if "mac_apps" not in ctx.cache:
        path = ctx.repo / "mac-apps" / "mac_apps.py"
        spec = importlib.util.spec_from_file_location("mac_apps", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ctx.cache["mac_apps"] = mod
    return ctx.cache["mac_apps"]


def launch_agents(ctx, refresh=False):
    """{kind: [job summary]} for cptr, watchdog, stuck-watch, sync-ai-sessions, mcp-tools."""
    if refresh or "agents" not in ctx.cache:
        ma = mac_apps(ctx)
        found = ma.detect()
        ctx.cache["agents"] = {k: [summarize_job(ma, j) for j in v] for k, v in found.items()}
        ctx.cache["agents_raw"] = found
    return ctx.cache["agents"]


def summarize_job(ma, j):
    st = ma.job_state(j["label"])
    env = j["job"].get("EnvironmentVariables") or {}
    app = j["app"]
    return {
        "label": j["label"], "plist": str(j["plist"]), "loaded": st is not None,
        "state": (st or {}).get("state"), "pid": (st or {}).get("pid"),
        "program": os.path.basename(j["inner"][0]) if j["inner"] else "",
        "runs": " ".join(os.path.basename(x) if i == 0 else x for i, x in enumerate(j["inner"][:2])),
        "env": {k: str(v) for k, v in env.items() if k in SAFE_ENV}, "env_keys": sorted(env),
        "keepalive": bool(j["job"].get("KeepAlive")),
        "named": ({"name": app["name"], "bundle_id": app["bundle_id"], "kind": app["kind"],
                   "probe_ok": app["probe_ok"], "exe": app["exe"], "app": str(app["app"])} if app else None),
    }


def launchd_loaded(ctx, label):
    return ctx.capture(["launchctl", "print", f"gui/{os.getuid()}/{label}"]).returncode == 0


def reload_agent(ctx, label, plist):
    dom = f"gui/{os.getuid()}"
    ctx.capture(["launchctl", "bootout", f"{dom}/{label}"])
    r = ctx.capture(["launchctl", "bootstrap", dom, str(plist)])
    return r.returncode == 0, r.stderr.strip()


# --------------------------------------------------------------------------------------- systemd
def systemd_state(ctx, unit):
    """{"enabled": str, "active": str} from systemctl --user (e.g. enabled/disabled/not-found)."""
    en = ctx.capture(["systemctl", "--user", "is-enabled", unit])
    ac = ctx.capture(["systemctl", "--user", "is-active", unit])
    return {"enabled": (en.stdout.strip() or ("not-found" if en.returncode else "")),
            "active": ac.stdout.strip() or "unknown"}


def unit_env(path, key):
    """Last Environment=KEY=value in a unit file (and its .d/*.conf drop-ins)."""
    val = None
    files = [Path(path)] + sorted(Path(str(path) + ".d").glob("*.conf"))
    for f in files:
        try:
            for line in f.read_text().splitlines():
                m = re.match(rf"\s*Environment=\"?{re.escape(key)}=([^\"\s]+)", line)
                if m:
                    val = m.group(1)
        except OSError:
            pass
    return val


# ------------------------------------------------------------------------------------------ cptr
def cptr_python_candidates(ctx, install):
    home = ctx.home
    uv = [home / ".local/share/uv/tools/cptr/bin/python"]
    pipx = [home / ".local/pipx/venvs/cptr/bin/python", home / ".local/share/pipx/venvs/cptr/bin/python",
            home / "Library/Application Support/pipx/venvs/cptr/bin/python"]
    return {"uv": uv, "pipx": pipx}.get(install, uv + pipx)


def cptr_version(ctx, install="auto"):
    """(version, install method) from the installed package metadata, or (None, None)."""
    for py in cptr_python_candidates(ctx, install):
        if py.exists():
            r = ctx.capture([str(py), "-c", "import importlib.metadata as m; print(m.version('cptr'))"], timeout=15)
            if r.returncode == 0:
                return r.stdout.strip(), ("pipx" if "pipx" in str(py) else "uv")
    return None, None
