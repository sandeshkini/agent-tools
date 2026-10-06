"""cptr: report only. Is cptr's service registered, answering, and which version runs.

Setup never installs, upgrades or restarts cptr itself: that's the cptr fork's build + swap scripts
(detached, health-gated, with rollback). This component tells you whether the baseline is there.
"""
from ..core import GAP, OK, Component, Plan
from .. import host
from . import _cptr


class Cptr(Component):
    name = "cptr"
    description = "report only: cptr's launchd job / systemd unit is registered and answering (never changed by setup)"
    order = 60

    def detect(self):
        ctx = self.ctx
        t = _cptr.target(ctx)
        f = {"label": t["label"], "port": t["port"], "how": t["how"], "service": t["service"]}
        if ctx.os == "darwin":
            jobs = t["jobs"]
            f["found"] = [j["label"] for j in jobs]
            j = next((x for x in jobs if x["label"] == t["label"]), None)
            f["loaded"] = bool(j and j["loaded"])
            f["named"] = (j or {}).get("named")
        else:
            st = host.systemd_state(ctx, t["service"])
            f["found"] = [t["service"]] if st["enabled"] not in ("not-found", "") else []
            f["loaded"] = st["active"] == "active"
            f["enabled"] = st["enabled"]
        f["healthy"] = host.cptr_healthy(t["port"])
        f["version"], f["install"] = host.cptr_version(ctx, t["install"])
        return f

    def plan(self, f):
        where = f["label"] if self.ctx.os == "darwin" else f"{f['service']}.service"
        if not f["found"]:
            return Plan(GAP, "no cptr service found", human=["install cptr (see the cptr fork's FORK.md); setup doesn't"])
        if self.ctx.os == "darwin" and not f["label"]:
            return Plan(GAP, f"more than one cptr LaunchAgent ({', '.join(f['found'])})",
                        human=["set [cptr] label in the profile"])
        if self.ctx.os == "darwin" and f["label"] not in f["found"]:
            return Plan(GAP, f"profile says {f['label']} but found {', '.join(f['found'])}",
                        human=["fix [cptr] label in the profile"])
        ver = f"cptr {f['version']} ({f['install']})" if f["version"] else "cptr (version unknown)"
        if f["healthy"]:
            return Plan(OK, f"{ver} via {where} on :{f['port']}, answering")
        state = "loaded" if f["loaded"] else "not running"
        return Plan(GAP, f"{ver} via {where}: {state}, NOT answering on :{f['port']}",
                    human=["setup doesn't restart cptr; the watchdog should. Check its log"])
