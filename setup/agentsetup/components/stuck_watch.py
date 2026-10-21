"""stuck-watch: now the machine-health job (stuck-watch + mem-reaper + stale-prompts + docker-hygiene; machine-health/install.sh).
The component keeps its old name so profiles and named-apps don't change."""
from ..core import Component
from .. import host
from ._jobs import plan_launch_agent

LABEL = "com.sandesh.machine-health"


class StuckWatch(Component):
    name = "stuck-watch"
    description = "machine-health LaunchAgent: stuck agent commands, memory reaper, stale permission prompts, docker report"
    platforms = ("darwin",)
    order = 30

    def not_applicable(self):
        if self.ctx.os == "linux":
            return "not applicable on Linux (it watches macOS TCC prompts and dialogs)"
        return super().not_applicable()

    def detect(self):
        return {"jobs": host.launch_agents(self.ctx)["stuck-watch"]}

    def plan(self, f):
        return plan_launch_agent(f["jobs"], "run machine-health/install.sh (LaunchAgent " + LABEL + ")", str(self.ctx.home))

    def owned_files(self):
        return [self.ctx.home / f"Library/LaunchAgents/{LABEL}.plist", self.ctx.home / ".config/machine-health/config"]

    def apply(self, plan):
        if plan.data.get("do") == "load":
            good, err = host.reload_agent(self.ctx, plan.data["label"], plan.data["plist"])
            if not good:
                self.ctx.say(f"      launchctl bootstrap failed: {err}")
            return good
        return self.ctx.run([self.ctx.repo / "machine-health/install.sh"]) == 0
