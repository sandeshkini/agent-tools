"""Push the result through ntfy, with the same settings discovery as Stuck Watch and mac-apps.

Order: NTFY_URL / NTFY_TOPIC / NTFY_TOKEN in the environment; the profile's [ntfy] url/topic and its
[secrets] env_file; ~/.config/stuck-watch/config; an installed mcp-tools LaunchAgent's environment;
this clone's .env (the Docker host). Nothing configured = no push (the log still has everything).
"""
import json
import urllib.request

from .core import read_env_file

MCP_PLISTS = ("com.sandesh.mcp-tools.plist", "com.sandesh.pa.agent-tools-mcp.plist")


def config(ctx):
    sources = []
    sec = ctx.secrets()
    sources.append({k: sec[k] for k in ("NTFY_URL", "NTFY_TOPIC", "NTFY_TOKEN") if sec.get(k)})
    prof = ctx.section("ntfy")
    sources.append({"NTFY_URL": prof.get("url"), "NTFY_TOPIC": prof.get("topic")})
    sources.append(read_env_file(ctx.home / ".config/stuck-watch/config"))
    if ctx.os == "darwin":
        import plistlib
        for name in MCP_PLISTS:
            try:
                with open(ctx.home / "Library/LaunchAgents" / name, "rb") as f:
                    sources.append(plistlib.load(f).get("EnvironmentVariables") or {})
            except Exception:
                pass
    sources.append(read_env_file(ctx.repo / ".env"))

    def first(key):
        return next((src[key] for src in sources if src.get(key)), None)
    cfg = {"url": first("NTFY_URL"), "topic": first("NTFY_TOPIC"), "token": first("NTFY_TOKEN")}
    if not cfg.get("url") or str(cfg["url"]).startswith("http://ntfy"):   # the compose-internal name
        return None
    cfg["topic"] = cfg.get("topic") or "aibo"
    return cfg


def send(ctx, title, message, priority=3):
    cfg = config(ctx)
    if not cfg:
        ctx.log("ntfy: not configured, no push")
        return False
    headers = {"Content-Type": "application/json"}
    if cfg.get("token"):
        headers["Authorization"] = f"Bearer {cfg['token']}"
    body = json.dumps({"topic": cfg["topic"], "title": title, "message": message[:3500],
                       "priority": priority, "tags": ["wrench"]}).encode()
    try:
        req = urllib.request.Request(cfg["url"].rstrip("/") + "/", data=body, method="POST", headers=headers)
        urllib.request.urlopen(req, timeout=10).read()
        ctx.log("ntfy: sent")
        return True
    except Exception as e:
        ctx.log(f"ntfy failed: {type(e).__name__}")
        return False
