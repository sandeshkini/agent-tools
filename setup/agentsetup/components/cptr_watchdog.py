"""cptr-watchdog: re-registers / restarts cptr's service if it vanishes or stops answering.

macOS: a LaunchAgent (cptr-watchdog/install.sh, or a named app). Its target is set by
CPTR_WATCHDOG_LABEL / CPTR_WATCHDOG_PORT in the plist's environment (defaults com.cptr.run :8000);
setup sets them when the profile's cptr label/port differ. Linux: the systemd --user timer from the same
installer; a port other than 8899 goes in a drop-in (survives re-installs).
"""
import os
import plistlib

from ..core import CHANGE, GAP, OK, Component, Plan
from .. import host
from . import _cptr
from ._jobs import describe

LABEL = "com.cptr.watchdog"
MAC_DEFAULT = ("com.cptr.run", 8000)
LINUX_DEFAULT_PORT = 8899
DROPIN = "cptr-watchdog.service.d/agent-setup.conf"


class CptrWatchdog(Component):
    name = "cptr-watchdog"
    description = "periodic self-heal for cptr's launchd job / systemd unit (cptr-watchdog/install.sh)"
    order = 40

    def units_dir(self):
        return self.ctx.home / ".config/systemd/user"

    def detect(self):
        t = _cptr.target(self.ctx)
        if self.ctx.os == "darwin":
            return {"jobs": host.launch_agents(self.ctx)["watchdog"], "target_label": t["label"] or MAC_DEFAULT[0],
                    "target_port": t["port"], "cptr_jobs": [j["label"] for j in t["jobs"]]}
        svc = self.units_dir() / "cptr-watchdog.service"
        return {"timer": host.systemd_state(self.ctx, "cptr-watchdog.timer"), "service_file": svc.is_file(),
                "port": host.unit_env(svc, "CPTR_WATCHDOG_PORT"), "target_port": t["port"]}

    # ------------------------------------------------------------------------------------ plan
    def plan(self, f):
        return self.plan_linux(f) if self.ctx.os == "linux" else self.plan_mac(f)

    def plan_mac(self, f):
        want = (f["target_label"], int(f["target_port"]))
        if want[0] not in f.get("cptr_jobs", [want[0]]):
            found = ", ".join(f["cptr_jobs"]) or "none"
            return Plan(GAP, f"cptr's LaunchAgent {want[0]} doesn't exist (found: {found}); not touching the watchdog",
                        human=["install cptr, or fix [cptr] label in the profile"])
        set_env = want != MAC_DEFAULT
        jobs = f["jobs"]
        if not jobs:
            acts = [f"run cptr-watchdog/install.sh (LaunchAgent {LABEL}, every 60 s)"]
            if set_env:
                acts.append(f"set CPTR_WATCHDOG_LABEL={want[0]} CPTR_WATCHDOG_PORT={want[1]} in its plist and reload it")
            return Plan(CHANGE, "not installed", actions=acts, data={"do": "install", "set_env": set_env})
        j = next((x for x in jobs if x["loaded"]), jobs[0])
        have = (j["env"].get("CPTR_WATCHDOG_LABEL", MAC_DEFAULT[0]), int(j["env"].get("CPTR_WATCHDOG_PORT", MAC_DEFAULT[1])))
        acts, problems = [], []
        if have != want:
            problems.append(f"watches {have[0]} :{have[1]} but cptr is {want[0]} :{want[1]}")
            acts.append(f"set CPTR_WATCHDOG_LABEL={want[0]} CPTR_WATCHDOG_PORT={want[1]} in "
                        f"{j['plist'].replace(str(self.ctx.home), '~')} and reload the watchdog (cptr isn't restarted)")
        if not j["loaded"]:
            problems.append("installed but not loaded")
            if not acts:
                acts.append("load it again (launchctl bootstrap)")
        if acts:
            return Plan(CHANGE, "; ".join(problems), actions=acts,
                        data={"do": "fix", "label": j["label"], "plist": j["plist"], "set_env": have != want})
        return Plan(OK, f"{describe(j, str(self.ctx.home))}, watching {want[0]} :{want[1]}")

    def plan_linux(self, f):
        want = int(f["target_port"])
        have = int(f["port"] or LINUX_DEFAULT_PORT)
        install = f["timer"].get("enabled") != "enabled"
        acts = ["run cptr-watchdog/install.sh (systemd --user cptr-watchdog.timer, every 60 s)"] if install else []
        if have != want:
            acts.append(f"write ~/.config/systemd/user/{DROPIN} with CPTR_WATCHDOG_PORT={want}")
        if not acts:
            return Plan(OK, f"cptr-watchdog.timer enabled ({f['timer'].get('active')}), watching cptr :{want}")
        why = "not installed" if install else f"watches :{have} but cptr is on :{want}"
        return Plan(CHANGE, why, actions=acts, data={"do": "install" if install else "fix", "port": want,
                                                     "dropin": have != want})

    # ----------------------------------------------------------------------------------- apply
    def owned_files(self):
        if self.ctx.os == "linux":
            d = self.units_dir()
            return [d / "cptr-watchdog.service", d / "cptr-watchdog.timer", d / DROPIN]
        files = [self.ctx.home / f"Library/LaunchAgents/{LABEL}.plist", self.ctx.home / ".local/bin/cptr-watchdog.sh"]
        files += [j["plist"] for j in host.launch_agents(self.ctx)["watchdog"] if j["plist"] not in map(str, files)]
        return files

    def apply(self, plan):
        ctx, d = self.ctx, plan.data
        if ctx.os == "linux":
            if d["do"] == "install" and ctx.run([ctx.repo / "cptr-watchdog/install.sh"]) != 0:
                return False
            if d["dropin"]:
                p = self.units_dir() / DROPIN
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f"# Written by agent-tools setup (profile [cptr] port)\n[Service]\n"
                             f"Environment=CPTR_WATCHDOG_PORT={d['port']}\n")
                ctx.capture(["systemctl", "--user", "daemon-reload"])
                ctx.say(f"      wrote {str(p).replace(str(ctx.home), '~')}")
            return True
        if d["do"] == "install":
            if ctx.run([ctx.repo / "cptr-watchdog/install.sh"]) != 0:
                return False
            if not d.get("set_env"):
                return True
            jobs = host.launch_agents(ctx, refresh=True)["watchdog"]
            if not jobs:
                return False
            d = {"label": jobs[0]["label"], "plist": jobs[0]["plist"], "set_env": True}
        t = _cptr.target(ctx)
        if d.get("set_env"):
            with open(d["plist"], "rb") as fh:
                job = plistlib.load(fh)
            env = job.setdefault("EnvironmentVariables", {})
            env["CPTR_WATCHDOG_LABEL"] = t["label"] or MAC_DEFAULT[0]
            env["CPTR_WATCHDOG_PORT"] = str(t["port"])
            mode = os.stat(d["plist"]).st_mode & 0o777
            with open(d["plist"], "wb") as fh:
                plistlib.dump(job, fh)
            os.chmod(d["plist"], mode)
            ctx.say(f"      set CPTR_WATCHDOG_LABEL/PORT in {d['plist'].replace(str(ctx.home), '~')}")
        good, err = host.reload_agent(ctx, d["label"], d["plist"])
        if not good:
            ctx.say(f"      launchctl bootstrap failed: {err}")
        host.launch_agents(ctx, refresh=True)
        return good
