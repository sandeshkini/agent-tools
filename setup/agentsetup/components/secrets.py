"""secrets: render the files that need secrets from the one store (D4, Sandesh 2026-10-07).

The store is ~/Documents/secrets/keys.env, read through agent-tools/bin/fleet-secret. Files that have
to hold a secret (a compose .env, cptr.env, ...) are *generated*: a template with ${NAME} placeholders
lives in the machine's profile folder, and this step renders it with `fleet-secret render`.

Profile:
    [secrets.render]
    cptr = { template = "{profile_dir}/env/cptr.env", out = "~/.cptr/cptr.env", note = "restart cptr to apply" }

Docs: aibo-server/infrastructure/secrets.md
"""
import os
import subprocess
import tempfile
from pathlib import Path

from ..core import CHANGE, GAP, OK, Component, Plan


class Secrets(Component):
    name = "secrets"
    description = "render files that hold secrets (compose .env, cptr.env, ...) from keys.env via fleet-secret"
    order = 5

    def tool(self):
        return str(self.ctx.repo / "bin/fleet-secret")

    def env(self):
        e = dict(os.environ)
        f = self.ctx.section("secrets").get("env_file")
        if f:
            e["FLEET_SECRETS_FILE"] = self.ctx.expand(f)
        return e

    def items(self):
        out = []
        for name, r in (self.ctx.section("secrets").get("render") or {}).items():
            out.append((name, Path(self.ctx.expand(r["template"])), Path(self.ctx.expand(r["out"])), r.get("note", "")))
        return out

    def render_text(self, tpl):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "out"
            r = subprocess.run([self.tool(), "render", str(tpl), str(p)], capture_output=True, text=True, env=self.env())
            if r.returncode != 0:
                raise ValueError(r.stderr.strip())
            return p.read_text()

    def detect(self):
        f = {"items": []}
        for name, tpl, out, note in self.items():
            it = {"name": name, "template": str(tpl), "out": str(out), "note": note}
            if not tpl.is_file():
                it["error"] = f"template {tpl} is missing"
            else:
                try:
                    want = self.render_text(tpl)
                    have = out.read_text() if out.is_file() else None
                    it["same"] = have == want
                    it["exists"] = have is not None
                    it["mode_ok"] = (not out.exists()) or (out.stat().st_mode & 0o077) == 0
                except (OSError, ValueError) as ex:
                    it["error"] = str(ex)
            f["items"].append(it)
        return f

    def plan(self, f):
        if not f["items"]:
            return Plan(OK, "nothing to render (no [secrets.render] in the profile)")
        actions, notes, data, errors = [], [], [], []
        for it in f["items"]:
            if it.get("error"):
                errors.append(f"{it['name']}: {it['error']}")
                continue
            if it["same"] and it["mode_ok"]:
                continue
            actions.append(f"render {it['out']} from {it['template']}" + ("" if it["exists"] else " (new)"))
            if it.get("note"):
                notes.append(f"{it['name']}: {it['note']}")
            data.append(it)
        if errors:
            return Plan(GAP, "; ".join(errors), human=["add the missing names: fleet-secret set NAME"], actions=actions)
        if not actions:
            return Plan(OK, f"{len(f['items'])} file(s) match keys.env")
        return Plan(CHANGE, f"{len(actions)} file(s) differ from what keys.env gives", actions=actions, notes=notes,
                    data={"items": data})

    def owned_files(self):
        return [Path(o) for _, _, o, _ in self.items()]

    def apply(self, plan):
        ok = True
        for it in plan.data.get("items", []):
            r = subprocess.run([self.tool(), "render", it["template"], it["out"]], capture_output=True, text=True,
                               env=self.env())
            self.ctx.say(f"      {it['out']}: {'rendered' if r.returncode == 0 else 'FAILED ' + r.stderr.strip()}")
            ok &= r.returncode == 0
        return ok
