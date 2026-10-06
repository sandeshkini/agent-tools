"""stuck-watch: notices agent commands stuck on a macOS prompt (stuck-watch/install.sh)."""
from ..core import Component
from .. import host
from ._jobs import plan_launch_agent

LABEL = "com.sandesh.stuck-watch"


class StuckWatch(Component):
    name = "stuck-watch"
    description = "Stuck Watch LaunchAgent: one ntfy push per agent command stuck on a macOS prompt"
    platforms = ("darwin",)
    order = 30

    def not_applicable(self):
        if self.ctx.os == "linux":
            return "not applicable on Linux (it watches macOS TCC prompts and dialogs)"
        return super().not_applicable()

    def detect(self):
        return {"jobs": host.launch_agents(self.ctx)["stuck-watch"]}

    def plan(self, f):
        return plan_launch_agent(f["jobs"], "run stuck-watch/install.sh (LaunchAgent " + LABEL + ")", str(self.ctx.home))

    def owned_files(self):
        return [self.ctx.home / f"Library/LaunchAgents/{LABEL}.plist", self.ctx.home / ".config/stuck-watch/config"]

    def apply(self, plan):
        if plan.data.get("do") == "load":
            good, err = host.reload_agent(self.ctx, plan.data["label"], plan.data["plist"])
            if not good:
                self.ctx.say(f"      launchctl bootstrap failed: {err}")
            return good
        return self.ctx.run([self.ctx.repo / "stuck-watch/install.sh"]) == 0
