"""computer-use: cua-driver (desktop apps) + agent-browser (web pages), via computer-use/install.sh."""
import os

from ..core import CHANGE, OK, Component, Plan
from .. import host

CUA_APP = "/Applications/CuaDriver.app/Contents/MacOS/cua-driver"
CUA_LABEL = "com.trycua.cua-driver"
CHROME_LABEL = "com.sandesh.agent-chrome"
CDP_PORT = 9333


class ComputerUse(Component):
    name = "computer-use"
    description = "cua-driver + its daemon, agent-browser, skills and MCP wiring; optional Agent Chrome (computer-use/install.sh)"
    platforms = ("darwin",)
    order = 10

    def not_applicable(self):
        if self.ctx.os == "linux":
            return "not applicable on Linux (it uses computer-use-linux + agent-browser, set up separately)"
        return super().not_applicable()

    def detect(self):
        ctx = self.ctx
        f = {"cua": None, "agent_browser": None}
        if os.access(CUA_APP, os.X_OK):
            r = ctx.capture([CUA_APP, "--version"], timeout=10)
            f["cua"] = (r.stdout.split() or ["?", "installed"])[-1] if r.returncode == 0 else "installed"
        f["daemon"] = host.launchd_loaded(ctx, CUA_LABEL)
        ab = host.which("agent-browser")
        if ab:
            r = ctx.capture([ab, "--version"], timeout=10)
            f["agent_browser"] = (r.stdout.split() or ["installed"])[-1] if r.returncode == 0 else "installed"
        f["claude"] = bool(host.which("claude"))
        f["claude_mcp"] = "cua-driver" in host.claude_mcp_servers(ctx)
        oc = host.opencode_config_text(ctx)
        f["opencode"] = (ctx.home / ".config/opencode").is_dir()
        f["opencode_mcp"] = bool(oc and '"cua-driver"' in oc)
        f["agent_chrome"] = host.launchd_loaded(ctx, CHROME_LABEL)
        f["cdp"] = host.http_status(f"http://127.0.0.1:{CDP_PORT}/json/version") == 200
        return f

    def plan(self, f):
        want_chrome = bool(self.cfg.get("agent_chrome"))
        missing = []
        if not f.get("cua"):
            missing.append("cua-driver")
        if not f.get("daemon"):
            missing.append(f"the cua-driver daemon ({CUA_LABEL})")
        if not f.get("agent_browser"):
            missing.append("agent-browser")
        if f.get("claude") and not f.get("claude_mcp"):
            missing.append("cua-driver MCP in Claude Code")
        if f.get("opencode") and not f.get("opencode_mcp"):
            missing.append("cua-driver MCP in OpenCode")
        if want_chrome and not f.get("agent_chrome"):
            missing.append(f"the Agent Chrome ({CHROME_LABEL}, CDP :{CDP_PORT})")
        notes = []
        if not want_chrome and f.get("agent_chrome"):
            notes.append(f"{CHROME_LABEL} is loaded although the profile doesn't ask for it (left alone)")
        if want_chrome and f.get("agent_chrome") and not f.get("cdp"):
            notes.append(f"Agent Chrome is loaded but nothing answers on CDP :{CDP_PORT}")
        if not missing:
            mcp = [n for n, k in (("Claude Code", "claude_mcp"), ("OpenCode", "opencode_mcp")) if f.get(k)]
            bits = [f"cua-driver {f['cua']}", "daemon running", f"agent-browser {f['agent_browser']}",
                    "MCP in " + (" + ".join(mcp) if mcp else "no agent CLI")]
            if f.get("agent_chrome"):
                bits.append(f"Agent Chrome :{CDP_PORT}")
            return Plan(OK, ", ".join(bits), notes=notes)
        cmd = "computer-use/install.sh" + (" --agent-chrome" if want_chrome else "")
        actions = [f"run {cmd} (adds only what's missing: {', '.join(missing)})"]
        if f.get("daemon"):
            notes.append("the installer restarts the cua-driver daemon (keeps its permissions)")
        human = []
        if not f.get("cua"):
            human.append("after install: `cua-driver permissions grant` (Accessibility + Screen Recording; a person must click)")
        if want_chrome and not f.get("agent_chrome"):
            human.append("log in to the sites the agents use in the Agent Chrome")
        return Plan(CHANGE, "missing: " + ", ".join(missing), actions=actions, human=human, notes=notes)

    def owned_files(self):
        la = self.ctx.home / "Library/LaunchAgents"
        files = [la / f"{CUA_LABEL}.plist"]
        if self.cfg.get("agent_chrome"):
            files += [la / f"{CHROME_LABEL}.plist", self.ctx.home / ".agent-browser/config.json"]
        return files

    def apply(self, plan):
        cmd = [self.ctx.repo / "computer-use/install.sh"] + (["--agent-chrome"] if self.cfg.get("agent_chrome") else [])
        return self.ctx.run(cmd) == 0
