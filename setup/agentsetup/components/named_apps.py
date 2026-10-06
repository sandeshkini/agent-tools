"""named-apps: run the other background jobs as named macOS apps (mac-apps --watchdog, --stuck-watch, ...).

A job that already runs as a named app (built by mac-apps or by Personal Agent's pa-app) is left alone:
rebuilding an app changes an ad-hoc signature and macOS then forgets its privacy grants.
"""
from ..core import CHANGE, OK, Component, Plan
from .. import host
from . import _cptr

# setup component name -> mac-apps job kind
KINDS = {"stuck-watch": "stuck-watch", "cptr-watchdog": "watchdog", "sync-ai-sessions": "sync-ai-sessions",
         "mcp-tools": "mcp-tools"}
DEFAULT_APPS = ["stuck-watch", "cptr-watchdog"]


class NamedApps(Component):
    name = "named-apps"
    description = "wrap Stuck Watch, the watchdog (and optionally session sync, mcp-tools) in named apps (mac-apps)"
    platforms = ("darwin",)
    order = 70

    def not_applicable(self):
        if self.ctx.os == "linux":
            return "not applicable on Linux (no app identities)"
        return super().not_applicable()

    def apps(self):
        apps = self.cfg.get("apps", DEFAULT_APPS)
        bad = [a for a in apps if a not in KINDS]
        if bad:
            raise ValueError(f"[components.named-apps] apps: unknown {', '.join(bad)} (choose from {', '.join(KINDS)})")
        return apps

    def detect(self):
        agents = host.launch_agents(self.ctx)
        return {"jobs": {a: agents[KINDS[a]] for a in self.apps()}}

    def plan(self, f):
        named, todo, missing = [], [], []
        for app, jobs in f["jobs"].items():
            if not jobs:
                missing.append(app)
                continue
            for j in jobs:
                (named if j["named"] else todo).append((app, j))
        notes = [f"{a}: no job installed yet (its own component installs it; re-run to wrap it)" for a in missing]
        if not todo:
            if not named:
                return Plan(OK, "nothing to wrap yet", notes=notes)
            return Plan(OK, ", ".join(f'{j["label"]} as "{j["named"]["name"]}"' for _, j in named), notes=notes)
        acts = [f"wrap {j['label']} ({a}) in a named app, back up and repoint its plist, reload; restore it if the job "
                "doesn't run" for a, j in todo]
        human = ["the first run of each new app may ask once for e.g. Documents: click Allow"]
        if any(a in ("sync-ai-sessions",) for a, _ in todo):
            human.append("session sync reads protected folders: grant its app Full Disk Access if its old "
                         "interpreter had it (else the wrap is rolled back when the run fails)")
        return Plan(CHANGE, f"{len(todo)} job(s) not named: " + ", ".join(j["label"] for _, j in todo),
                    actions=acts, human=human, notes=notes, data={"kinds": sorted({KINDS[a] for a, _ in todo})})

    def apply(self, plan):
        env = _cptr.mac_apps_env(self.ctx)
        ok = True
        for kind in plan.data["kinds"]:
            ok &= self.ctx.run([self.ctx.repo / "mac-apps/install.sh", f"--{kind}"], extra_env=env) == 0
        return ok

    def rollback(self):
        kinds = [KINDS[a] for a in self.apps()]
        return self.ctx.run([self.ctx.repo / "mac-apps/install.sh", "--rollback", *kinds],
                            extra_env=_cptr.mac_apps_env(self.ctx)) == 0
