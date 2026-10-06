"""Which cptr this machine runs: from the profile's [cptr] table, else detected."""
from .. import host

DEFAULT_PORT = {"darwin": 8000, "linux": 8899}


def target(ctx):
    """{"label", "port", "service", "install", "jobs", "how"}. jobs: cptr LaunchAgents found (macOS).
    label: the profile's, else the one detected job (None if zero or several)."""
    if "cptr_target" in ctx.cache:
        return ctx.cache["cptr_target"]
    c = ctx.section("cptr")
    t = {"label": c.get("label"), "service": c.get("service", "cptr"), "install": c.get("install", "auto"),
         "port": c.get("port"), "jobs": [], "how": "profile"}
    if ctx.os == "darwin":
        t["jobs"] = host.launch_agents(ctx)["cptr"]
        if not t["label"] and len(t["jobs"]) == 1:
            t["label"], t["how"] = t["jobs"][0]["label"], "detected"
        if t["port"] is None and t["label"]:
            raw = [j for j in ctx.cache.get("agents_raw", {}).get("cptr", []) if j["label"] == t["label"]]
            if raw:
                t["port"], t["how"] = host.mac_apps(ctx).cptr_port(raw[0])[0], "detected"
    if t["port"] is None:
        t["port"] = DEFAULT_PORT.get(ctx.os, 8000)
    t["port"] = int(t["port"])
    ctx.cache["cptr_target"] = t
    return t


def mac_apps_env(ctx):
    """Environment for agent-tools/mac-apps from the profile ([named_apps] and [cptr])."""
    na = ctx.section("named_apps")
    c = ctx.section("cptr")
    env = {"MAC_APPS_DIR": ctx.expand(na.get("dir")) if na.get("dir") else None,
           "MAC_APPS_BUNDLE_PREFIX": na.get("bundle_prefix"),
           "MAC_APPS_SIGN_IDENTITY": na.get("sign_identity"),
           "MAC_APPS_CPTR_LABEL": c.get("label"),
           "MAC_APPS_CPTR_PORT": c.get("port")}
    return {k: str(v) for k, v in env.items() if v not in (None, "")}
