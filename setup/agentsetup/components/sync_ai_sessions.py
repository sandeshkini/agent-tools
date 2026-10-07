"""sync-ai-sessions: exports Claude Code / OpenCode sessions into the ai-memory repo every 15 min.

macOS: sync-ai-sessions/install.sh (LaunchAgent), or any job already doing it (a named app, or a
hand-made LaunchAgent). Linux: agent-tools has no installer; the profile can name one (`installer`),
otherwise a missing timer is reported for a person to set up.
"""
from ..core import Component
from .. import host
from ._jobs import plan_launch_agent, plan_systemd

LABEL = "com.sandesh.sync-ai-sessions"
TIMER = "sync-ai-sessions.timer"
REPO_PLACES = ("Documents/ai-memory", "Documents/personal/ai-memory")


class SyncAiSessions(Component):
    name = "sync-ai-sessions"
    description = "session/memory export into the ai-memory repo, every 15 min (sync-ai-sessions/install.sh)"
    order = 50

    def detect(self):
        ctx = self.ctx
        if ctx.os == "linux":
            return {"timer": host.systemd_state(ctx, TIMER)}
        return {"jobs": host.launch_agents(ctx)["sync-ai-sessions"],
                "repo": next((p for p in REPO_PLACES if (ctx.home / p / ".git").is_dir()), None),
                "uv": bool(host.which("uv"))}

    def plan(self, f):
        if self.ctx.os == "linux":
            inst = self.cfg.get("installer")
            return plan_systemd(f["timer"], TIMER, f"run {inst}" if inst else None,
                                "agent-tools has no Linux installer for it; set it up by hand or set "
                                "[components.sync-ai-sessions] installer in the profile")
        blockers = []
        if not f["repo"]:
            blockers.append("clone the ai-memory repo to ~/Documents/personal/ai-memory first")
        if not f["uv"]:
            blockers.append("install uv first (brew install uv)")
        return plan_launch_agent(f["jobs"], f"run sync-ai-sessions/install.sh (LaunchAgent {LABEL}, every 15 min)",
                                 str(self.ctx.home), blockers)

    def owned_files(self):
        if self.ctx.os == "linux":
            d = self.ctx.home / ".config/systemd/user"
            return [d / "sync-ai-sessions.service", d / TIMER]
        return [self.ctx.home / f"Library/LaunchAgents/{LABEL}.plist"]

    def apply(self, plan):
        ctx = self.ctx
        if plan.data.get("do") == "load":
            good, err = host.reload_agent(ctx, plan.data["label"], plan.data["plist"])
            if not good:
                ctx.say(f"      launchctl bootstrap failed: {err}")
            return good
        if ctx.os == "linux":
            return ctx.run(["sh", "-c", ctx.expand(self.cfg["installer"])]) == 0
        return ctx.run([ctx.repo / "sync-ai-sessions/install.sh"]) == 0
