"""cptr-app: run cptr as a named macOS app (cptr.app), so its Full Disk Access belongs to it alone.

The switch is agent-tools/mac-apps (`install.sh --cptr`), in the order learned the hard way:
build cptr.app -> a person grants it Full Disk Access -> probe as the app until it can read a
Full-Disk-Access-only path -> back up the plist -> switch detached (survives the restart of the cptr
you may be running this from) -> health gate (HTTP, a protected-path probe as the app, still up after
~15 s) -> automatic rollback if any check fails. Same shape as the cptr fork's swap script.
"""
from ..core import CHANGE, GAP, OK, Component, Plan
from .. import host
from . import _cptr

FDA = "kTCCServiceSystemPolicyAllFiles"


class CptrApp(Component):
    name = "cptr-app"
    description = "cptr as cptr.app with its own Full Disk Access; detached, health-gated switch with rollback (mac-apps --cptr)"
    platforms = ("darwin",)
    order = 90                     # last: its restart can end the session running setup
    restarts_cptr = True

    def not_applicable(self):
        if self.ctx.os == "linux":
            return "not applicable on Linux (no app identities or Full Disk Access)"
        return super().not_applicable()

    def detect(self):
        ctx = self.ctx
        t = _cptr.target(ctx)
        j = next((x for x in t["jobs"] if x["label"] == t["label"]), None)
        f = {"label": t["label"], "port": t["port"], "job": bool(j), "named": (j or {}).get("named"),
             "fda": None, "probe": None}
        n = f["named"]
        if n:
            ma = host.mac_apps(ctx)
            rows = ma.tcc_rows()                      # None if this shell can't read TCC.db
            if rows is not None:
                f["fda"] = any(s == FDA and c == n["bundle_id"] and allowed for s, c, _, allowed in rows)
            if n["probe_ok"]:                          # launcher understands --probe: ask it directly
                good, _ = ma.probe(n["exe"], ma.fda_paths())
                f["probe"] = good
        return f

    def plan(self, f):
        if not f["job"]:
            return Plan(GAP, "no cptr LaunchAgent found" + (f" ({f['label']})" if f["label"] else ""),
                        human=["install cptr first, or set [cptr] label in the profile"])
        n = f["named"]
        if n:
            app = n["app"].replace(str(self.ctx.home), "~")
            who = f'runs as "{n["name"]}" ({n["bundle_id"]}, {n["kind"]})'
            if f["probe"] is True or (f["probe"] is None and f["fda"] is True):
                how = "probe as the app read a protected file" if f["probe"] else "granted in TCC.db"
                return Plan(OK, f"{f['label']} {who}; Full Disk Access: {how}")
            if f["probe"] is False or f["fda"] is False:
                return Plan(GAP, f"{f['label']} {who}, but the app has NO Full Disk Access",
                            human=[f"System Settings > Privacy & Security > Full Disk Access: add {app} and switch it on"])
            return Plan(OK, f"{f['label']} {who}",
                        notes=["couldn't confirm Full Disk Access from this shell (TCC.db unreadable, launcher has no --probe)"])
        return Plan(CHANGE, f"{f['label']} runs cptr directly (not as a named app)", actions=[
            "build cptr.app running the job's current command unchanged (mac-apps)",
            "open System Settings > Full Disk Access and wait (up to 10 min) until a probe as cptr.app can read "
            "a Full-Disk-Access-only file",
            "back up the plist, point it at cptr.app, restart cptr DETACHED (a session inside cptr drops here)",
            f"health gate: answers on :{f['port']}, protected-path probe as the app, still up after ~15 s; "
            "otherwise restore the old plist and restart",
        ], human=["be at the Mac to add cptr.app to Full Disk Access (password / Touch ID)"],
            notes=["result goes to ~/Library/Logs/mac-apps.log and ntfy"])

    def apply(self, plan):
        return self.ctx.run([self.ctx.repo / "mac-apps/install.sh", "--cptr"], extra_env=_cptr.mac_apps_env(self.ctx)) == 0

    def rollback(self):
        return self.ctx.run([self.ctx.repo / "mac-apps/install.sh", "--rollback", "cptr"],
                            extra_env=_cptr.mac_apps_env(self.ctx)) == 0
