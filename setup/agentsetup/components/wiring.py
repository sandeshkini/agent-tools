"""wiring: write agent-tools/registry.toml into every agent program on this machine.

Covers:
- MCP servers in Claude Code (`claude mcp`, user scope), OpenCode (the "mcp" block of opencode.json[c])
  and Antigravity (`agy mcp`).
- Skill links in ~/.claude/skills, ~/.config/opencode/skills, ~/.agents/skills (cptr, Codex) and
  ~/.gemini/config/skills (agy).
- Helper commands linked into ~/.local/bin (fleet-secret, pa-secret, agy-ask, ui-shot).
- Claude Code plugins ([plugins.*]): marketplace added, plugin installed and enabled at user scope.
  Plugins not in the registry are reported, never uninstalled.
- Global instructions ([instructions.*], e.g. memory/standing-rules.md): an @import line in a managed
  block of ~/.claude/CLAUDE.md, and the "instructions" list of opencode.json[c]. The block is ours;
  the rest of CLAUDE.md is left alone.

The registry is the source of truth (Sandesh, 2026-10-07, D3).
- MCP servers and skills that aren't in the registry are reported, not removed. The exception is a
  skill link that points into agent-tools or personal-agent but is no longer listed; that's ours and
  stale, so it is removed.
- Real folders and files are never overwritten.

Docs: aibo-server/infrastructure/agents/tools.md
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from ..core import CHANGE, GAP, OK, Component, Plan
from .. import host, tomlmini

SECRET_RE = re.compile(r"\{secret:", re.I)
BLOCK_START = "<!-- agent-tools instructions: managed by `setup.sh --only wiring` (registry.toml) -->"
BLOCK_END = "<!-- /agent-tools instructions -->"
BLOCK_RE = re.compile(re.escape(BLOCK_START) + r"\n(.*?)" + re.escape(BLOCK_END) + r"\n?", re.S)


def claude_md_imports(text):
    """The @import paths inside our managed block of a CLAUDE.md, or None if there's no block."""
    m = BLOCK_RE.search(text or "")
    if not m:
        return None
    return [ln[1:].strip() for ln in m.group(1).splitlines() if ln.startswith("@")]


def with_claude_md_imports(text, paths):
    """CLAUDE.md text with our managed block set to these imports (block removed when empty)."""
    text = text or ""
    block = (BLOCK_START + "\n" + "".join(f"@{p}\n" for p in paths) + BLOCK_END + "\n") if paths else ""
    if BLOCK_RE.search(text):
        return BLOCK_RE.sub(lambda _: block, text, count=1)
    if not block:
        return text
    return (text.rstrip("\n") + "\n\n" if text.strip() else "") + block


# ------------------------------------------------------------------------------------- helpers
def strip_jsonc(text):
    """Drop // and /* */ comments and trailing commas, leaving strings alone."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 2; continue
            if c == '"':
                in_str = False
            i += 1; continue
        if c == '"':
            in_str = True; out.append(c); i += 1; continue
        if text.startswith("//", i):
            j = text.find("\n", i); i = n if j < 0 else j; continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2); i = n if j < 0 else j + 2; continue
        out.append(c); i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def has_comments(text):
    return strip_jsonc(text) != re.sub(r",(\s*[}\]])", r"\1", text)


def _norm_env(e):
    return {k: str(v) for k, v in (e or {}).items()}


class Wiring(Component):
    name = "wiring"
    description = "agent-tools/registry.toml -> MCP servers, skills and helper commands in Claude Code, OpenCode, agy and cptr"
    order = 25

    # --------------------------------------------------------------------------- registry
    def registry_path(self):
        p = self.cfg.get("registry")
        return Path(self.ctx.expand(p)) if p else self.ctx.repo / "registry.toml"

    def machine(self):
        return (self.ctx.section("machine").get("name") or "").strip()

    def personal_agent(self):
        p = self.ctx.section("paths").get("personal_agent")
        if p:
            p = Path(self.ctx.expand(p))
            return p if p.is_dir() else None
        c = self.ctx.home / "Documents/personal/personal-agent"
        return c if c.is_dir() else None

    def npm_root(self):
        if "npm_root" not in self.ctx.cache:
            npm = host.which("npm")
            r = self.ctx.capture([npm, "root", "-g"], timeout=20) if npm else None
            self.ctx.cache["npm_root"] = r.stdout.strip() if r is not None and r.returncode == 0 else ""
        return self.ctx.cache["npm_root"]

    def expand(self, s, missing):
        if not isinstance(s, str):
            return s
        if SECRET_RE.search(s):
            raise ValueError("secrets can't go into agent configs; use `fleet-secret run NAME -- …`")
        repl = {"{agent_tools}": str(self.ctx.repo), "{home}": str(self.ctx.home)}
        if "{personal_agent}" in s:
            pa = self.personal_agent()
            if not pa:
                missing.add("personal_agent")
                return s
            repl["{personal_agent}"] = str(pa)
        if "{npm_root}" in s:
            nr = self.npm_root()
            if not nr:
                missing.add("npm_root")
                return s
            repl["{npm_root}"] = nr
        for k, v in repl.items():
            s = s.replace(k, v)
        return os.path.expanduser(s)

    def applies(self, e, program=None):
        m = e.get("machines")
        if m and "*" not in m and self.machine() not in m:
            return False
        if e.get("os") and self.ctx.os not in e["os"]:
            return False
        if program and e.get("programs") and program not in e["programs"]:
            return False
        return True

    def load(self):
        reg = tomlmini.loads(self.registry_path().read_text())
        want_mcp, held, want_skills, want_bin, problems = {}, [], {}, {}, []
        want_plugins = {}
        for name, e in (reg.get("plugins") or {}).items():
            if self.applies(e) and e.get("id"):
                want_plugins[name] = {"id": e["id"], "marketplace": e.get("marketplace")}
        self._want_plugins = want_plugins
        want_instr = {}
        for name, e in (reg.get("instructions") or {}).items():
            if not self.applies(e):
                continue
            missing = set()
            p = self.expand(e["path"], missing)
            if missing or not Path(p).is_file():
                problems.append(f"instructions {name}: {p if not missing else 'path unresolved'} isn't a file here")
                continue
            want_instr[name] = {"path": p, "programs": e.get("programs")}
        self._want_instr = want_instr
        for key, e in (reg.get("mcp") or {}).items():
            if not self.applies(e):
                continue
            name = e.get("name", key)
            if e.get("enabled") is False:
                held.append(name)
                continue
            missing = set()
            try:
                if "url" in e:
                    spec = {"type": "http", "url": self.expand(e["url"], missing)}
                else:
                    spec = {"type": "stdio", "command": self.expand(e["command"], missing),
                            "args": [self.expand(a, missing) for a in e.get("args", [])],
                            "env": {k: self.expand(str(v), missing) for k, v in (e.get("env") or {}).items()}}
            except ValueError as ex:
                problems.append(f"mcp {name}: {ex}")
                continue
            if missing:
                problems.append(f"mcp {name}: can't resolve {', '.join(sorted(missing))} here")
                continue
            spec["programs"] = e.get("programs")
            want_mcp[name] = spec
        for name, e in (reg.get("skills") or {}).items():
            if not self.applies(e):
                continue
            missing = set()
            p = self.expand(e["path"], missing)
            if missing or not Path(p, "SKILL.md").is_file():
                problems.append(f"skill {name}: {p if not missing else 'path unresolved'} has no SKILL.md here")
                continue
            want_skills[name] = {"path": p, "programs": e.get("programs")}
        for name, t in (reg.get("bin") or {}).items():
            want_bin[name] = self.expand(t, set())
        return reg.get("programs") or {}, want_mcp, held, want_skills, want_bin, problems

    # ----------------------------------------------------------------------------- detect
    def opencode_path(self):
        for n in ("opencode.jsonc", "opencode.json"):
            p = self.ctx.home / ".config/opencode" / n
            if p.is_file():
                return p
        return None

    def read_mcp(self, kind):
        """Current MCP servers of one program, normalised to the registry's spec shape."""
        ctx = self.ctx
        if kind == "claude-cli":
            d = host.read_json(ctx.home / ".claude.json") or {}
            out = {}
            for n, s in (d.get("mcpServers") or {}).items():
                if s.get("url"):
                    out[n] = {"type": "http", "url": s["url"]}
                else:
                    out[n] = {"type": "stdio", "command": s.get("command"), "args": list(s.get("args") or []),
                              "env": _norm_env(s.get("env"))}
            return out
        if kind == "opencode-json":
            p = self.opencode_path()
            if not p:
                return {}
            d = json.loads(strip_jsonc(p.read_text()) or "{}")
            out = {}
            for n, s in (d.get("mcp") or {}).items():
                if s.get("type") == "remote":
                    out[n] = {"type": "http", "url": s.get("url")}
                else:
                    cmd = list(s.get("command") or [])
                    out[n] = {"type": "stdio", "command": cmd[0] if cmd else None, "args": cmd[1:],
                              "env": _norm_env(s.get("environment"))}
                if s.get("enabled") is False:
                    out[n]["disabled"] = True
            return out
        if kind == "agy-cli":
            d = host.read_json(ctx.home / ".gemini/config/mcp_config.json") or {}
            out = {}
            for n, s in (d.get("mcpServers") or {}).items():
                if s.get("serverUrl") or s.get("url"):
                    out[n] = {"type": "http", "url": s.get("serverUrl") or s.get("url")}
                else:
                    out[n] = {"type": "stdio", "command": s.get("command"), "args": list(s.get("args") or []),
                              "env": _norm_env(s.get("env"))}
                if s.get("disabled"):
                    out[n]["disabled"] = True
            return out
        return {}

    def installed(self, prog):
        exe = {"claude-code": "claude", "opencode": "opencode", "antigravity": "agy"}.get(prog)
        if exe:
            return bool(host.which(exe) or (self.ctx.home / ".local/bin" / exe).exists())
        return True

    def detect(self):
        ctx = self.ctx
        try:
            programs, want_mcp, held, want_skills, want_bin, problems = self.load()
        except (OSError, ValueError) as ex:
            return {"error": f"registry: {ex}"}
        f = {"programs": {}, "want_mcp": want_mcp, "held": held, "want_skills": want_skills,
             "want_bin": want_bin, "problems": problems, "managed_roots": [str(ctx.repo)],
             "want_plugins": getattr(self, "_want_plugins", {}), "plugins": self.read_plugins(),
             "want_instr": getattr(self, "_want_instr", {})}
        pa = self.personal_agent()
        if pa:
            f["managed_roots"].append(str(pa))
        for prog, p in programs.items():
            info = {"installed": self.installed(prog), "mcp_kind": p.get("mcp"), "skills_dir": None}
            if info["installed"] and p.get("mcp"):
                try:
                    info["mcp"] = self.read_mcp(p["mcp"])
                except (OSError, ValueError) as ex:
                    info["mcp_error"] = str(ex)
            if info["installed"] and p.get("instructions"):
                info["instr_kind"] = p["instructions"]
                try:
                    info["instr"] = self.read_instructions(p["instructions"])
                except (OSError, ValueError) as ex:
                    info["instr_error"] = str(ex)
            if p.get("skills"):
                sd = Path(ctx.expand(p["skills"]))
                info["skills_dir"] = str(sd)
                links = {}
                if sd.is_dir():
                    for c in sd.iterdir():
                        links[c.name] = os.readlink(c) if c.is_symlink() else ("<dir>" if c.is_dir() else "<file>")
                info["skills"] = links
            f["programs"][prog] = info
        bins = {}
        for n in want_bin:
            p = ctx.home / ".local/bin" / n
            bins[n] = os.readlink(p) if p.is_symlink() else ("<file>" if p.exists() else None)
        f["bin"] = bins
        oc = self.opencode_path()
        if oc:
            txt = oc.read_text()
            f["opencode_file"] = str(oc)
            f["opencode_comments"] = has_comments(txt)
            f["opencode_inline_keys"] = sorted(set(re.findall(r'"apiKey"\s*:\s*"(?!\{env:)[^"]+"', txt))) != []
        return f

    def read_instructions(self, kind):
        """Instruction files a program loads: ours (managed block) for claude-md, all for opencode-json."""
        if kind == "claude-md":
            p = self.ctx.home / ".claude/CLAUDE.md"
            imports = claude_md_imports(p.read_text()) if p.is_file() else None
            return imports or []
        if kind == "opencode-json":
            p = self.opencode_path()
            if not p:
                return []
            return list(json.loads(strip_jsonc(p.read_text()) or "{}").get("instructions") or [])
        return []

    def read_plugins(self):
        """Claude Code's installed plugins, enabled flags and known marketplace repos."""
        if not self.installed("claude-code"):
            return None
        pd = self.ctx.home / ".claude/plugins"
        inst = (host.read_json(pd / "installed_plugins.json") or {}).get("plugins") or {}
        enabled = (host.read_json(self.ctx.home / ".claude/settings.json") or {}).get("enabledPlugins") or {}
        repos = {(m.get("source") or {}).get("repo") or (m.get("source") or {}).get("url")
                 for m in (host.read_json(pd / "known_marketplaces.json") or {}).values()}
        return {"installed": sorted(inst), "enabled": enabled, "repos": sorted(r for r in repos if r)}

    # ------------------------------------------------------------------------------- plan
    @staticmethod
    def same(cur, want):
        if cur is None or cur.get("disabled"):
            return False
        if want["type"] == "http":
            return cur.get("type") == "http" and cur.get("url") == want["url"]
        return (cur.get("type") == "stdio" and cur.get("command") == want["command"]
                and list(cur.get("args") or []) == want["args"] and _norm_env(cur.get("env")) == want["env"])

    def plan(self, f):
        if f.get("error"):
            return Plan(GAP, f["error"], human=["fix agent-tools/registry.toml"])
        ops, actions, notes = [], [], list(f.get("problems") or [])
        for name in f.get("held") or []:
            notes.append(f"{name}: on hold in the registry (enabled = false)")
        for prog, info in f["programs"].items():
            if not info["installed"]:
                notes.append(f"{prog}: not installed here, skipped")
                continue
            if info.get("mcp_kind"):
                if info.get("mcp_error"):
                    notes.append(f"{prog}: can't read its MCP config ({info['mcp_error']})")
                else:
                    cur = info.get("mcp") or {}
                    for name, spec in f["want_mcp"].items():
                        if spec.get("programs") and prog not in spec["programs"]:
                            continue
                        if not self.same(cur.get(name), spec):
                            verb = "update" if name in cur else "add"
                            ops.append({"op": "mcp", "prog": prog, "kind": info["mcp_kind"], "name": name, "spec": spec})
                            actions.append(f"{prog}: {verb} MCP server {name}")
                    extra = sorted(set(cur) - set(f["want_mcp"]) - set(f.get("held") or []))
                    if extra:
                        notes.append(f"{prog}: MCP server(s) not in the registry: {', '.join(extra)} (add them there or remove them)")
            if info.get("skills_dir") is not None:
                links = info.get("skills") or {}
                for name, s in f["want_skills"].items():
                    if s.get("programs") and prog not in s["programs"]:
                        continue
                    have = links.get(name)
                    if have == s["path"]:
                        continue
                    if have in ("<dir>", "<file>"):
                        notes.append(f"{prog}: {info['skills_dir']}/{name} is a real {have[1:-1]}; left alone")
                        continue
                    ops.append({"op": "link", "path": f"{info['skills_dir']}/{name}", "target": s["path"]})
                    actions.append(f"{prog}: link skill {name}")
                for name, have in links.items():
                    if name in f["want_skills"] or have in ("<dir>", "<file>"):
                        continue
                    if any(have.startswith(r + "/") for r in f["managed_roots"]):
                        ops.append({"op": "unlink", "path": f"{info['skills_dir']}/{name}"})
                        actions.append(f"{prog}: remove stale skill link {name}")
        for name, target in f["want_bin"].items():
            have = f["bin"].get(name)
            if have == target:
                continue
            if have == "<file>":
                notes.append(f"~/.local/bin/{name} is a real file; left alone")
                continue
            ops.append({"op": "link", "path": str(self.ctx.home / ".local/bin" / name), "target": target})
            actions.append(f"link ~/.local/bin/{name}")
        for prog, info in f["programs"].items():
            kind = info.get("instr_kind")
            if not info["installed"] or not kind:
                continue
            if info.get("instr_error"):
                notes.append(f"{prog}: can't read its instructions ({info['instr_error']})")
                continue
            want = [s["path"] for s in (f.get("want_instr") or {}).values()
                    if not s.get("programs") or prog in s["programs"]]
            cur = info.get("instr") or []
            if kind == "claude-md":
                if cur != want:
                    ops.append({"op": "claude-md", "paths": want})
                    actions.append(f"{prog}: set instructions in ~/.claude/CLAUDE.md ({len(want)} file(s))")
            elif kind == "opencode-json":
                ours = [c for c in cur if any(c.startswith(r + "/") for r in f["managed_roots"])]
                new = [c for c in cur if c not in ours or c in want] + [w for w in want if w not in cur]
                if new != cur:
                    ops.append({"op": "opencode-instr", "list": new})
                    actions.append(f"{prog}: set instructions in opencode.json ({len(want)} file(s))")
        cur_pl = f.get("plugins")
        if f.get("want_plugins") and cur_pl is None:
            notes.append("claude-code: not installed here, plugins skipped")
        elif cur_pl is not None:
            for name, w in f.get("want_plugins", {}).items():
                if w.get("marketplace") and w["marketplace"] not in cur_pl["repos"]:
                    ops.append({"op": "marketplace", "source": w["marketplace"]})
                    actions.append(f"claude-code: add plugin marketplace {w['marketplace']}")
                if w["id"] not in cur_pl["installed"]:
                    ops.append({"op": "plugin", "id": w["id"]})
                    actions.append(f"claude-code: install plugin {w['id']}")
                elif cur_pl["enabled"].get(w["id"]) is False:
                    ops.append({"op": "plugin-enable", "id": w["id"]})
                    actions.append(f"claude-code: enable plugin {w['id']}")
            wanted = {w["id"] for w in f.get("want_plugins", {}).values()}
            extra = sorted(set(cur_pl["installed"]) - wanted)
            if extra:
                notes.append(f"claude-code: plugin(s) not in the registry: {', '.join(extra)} (add them there or uninstall them)")
        if f.get("opencode_inline_keys"):
            notes.append(f"{f['opencode_file']} has an API key written into it; use \"{{env:NAME}}\" (D4)")
        if ops and f.get("opencode_comments") and any(o.get("kind") == "opencode-json" or o["op"] == "opencode-instr" for o in ops):
            notes.append(f"{f['opencode_file']} has comments; rewriting it drops them (a backup is kept)")
        n_mcp = len(f["want_mcp"]); n_sk = len(f["want_skills"]); n_pl = len(f.get("want_plugins") or {})
        n_in = len(f.get("want_instr") or {})
        if not ops:
            return Plan(OK, f"{n_mcp} MCP server(s), {n_sk} skill(s), {n_pl} plugin(s), {n_in} instruction file(s) and {len(f['want_bin'])} command(s) match the registry", notes=notes)
        return Plan(CHANGE, f"{len(ops)} change(s) to match the registry", actions=actions, notes=notes, data={"ops": ops})

    # ------------------------------------------------------------------------------ apply
    def owned_files(self):
        h = self.ctx.home
        files = [h / ".claude.json", h / ".gemini/config/mcp_config.json", h / ".claude/CLAUDE.md"]
        oc = self.opencode_path()
        if oc:
            files.append(oc)
        return files

    def _claude(self, name, spec):
        claude = host.which("claude") or str(self.ctx.home / ".local/bin/claude")
        self.ctx.capture([claude, "mcp", "remove", "-s", "user", name], timeout=30)
        if spec["type"] == "http":
            j = {"type": "http", "url": spec["url"]}
        else:
            j = {"type": "stdio", "command": spec["command"], "args": spec["args"], "env": spec["env"]}
        r = self.ctx.capture([claude, "mcp", "add-json", "-s", "user", name, json.dumps(j)], timeout=30)
        return r.returncode == 0, (r.stderr or r.stdout).strip()

    def _agy(self, name, spec):
        agy = host.which("agy") or str(self.ctx.home / ".local/bin/agy")
        self.ctx.capture([agy, "mcp", "remove", name], timeout=30)
        if spec["type"] == "http":
            cmd = [agy, "mcp", "add", name, spec["url"]]
        else:
            cmd = [agy, "mcp", "add"] + sum((["--env", f"{k}={v}"] for k, v in spec["env"].items()), []) + \
                  [name, "--", spec["command"], *spec["args"]]
        r = self.ctx.capture(cmd, timeout=30)
        return r.returncode == 0, (r.stderr or r.stdout).strip()

    def _opencode(self, items, instructions=None):
        p = self.opencode_path() or (self.ctx.home / ".config/opencode/opencode.json")
        d = json.loads(strip_jsonc(p.read_text()) or "{}") if p.exists() else {"$schema": "https://opencode.ai/config.json"}
        if instructions is not None:
            if instructions:
                d["instructions"] = instructions
            else:
                d.pop("instructions", None)
        mcp = d.setdefault("mcp", {}) if items else d.get("mcp", {})
        for name, spec in items:
            if spec["type"] == "http":
                mcp[name] = {"type": "remote", "url": spec["url"], "enabled": True}
            else:
                e = {"type": "local", "enabled": True, "command": [spec["command"], *spec["args"]]}
                if spec["env"]:
                    e["environment"] = spec["env"]
                mcp[name] = e
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(d, indent=2) + "\n")
        os.replace(tmp, p)
        return True, ""

    def apply(self, plan):
        ok = True
        oc_items, oc_instr = [], None
        for o in plan.data.get("ops", []):
            if o["op"] == "mcp":
                if o["kind"] == "claude-cli":
                    good, msg = self._claude(o["name"], o["spec"])
                elif o["kind"] == "agy-cli":
                    good, msg = self._agy(o["name"], o["spec"])
                else:
                    oc_items.append((o["name"], o["spec"])); continue
                self.ctx.say(f"      {o['prog']}: {o['name']} {'ok' if good else 'FAILED: ' + msg}")
                ok &= good
            elif o["op"] == "link":
                p = Path(o["path"])
                p.parent.mkdir(parents=True, exist_ok=True)
                if p.is_symlink():
                    p.unlink()
                if p.exists():
                    self.ctx.say(f"      {p} exists and isn't a link; skipped"); ok = False; continue
                p.symlink_to(o["target"])
                self.ctx.say(f"      {p} -> {o['target']}")
            elif o["op"] in ("marketplace", "plugin", "plugin-enable"):
                claude = host.which("claude") or str(self.ctx.home / ".local/bin/claude")
                cmd = {"marketplace": [claude, "plugin", "marketplace", "add", o.get("source", "")],
                       "plugin": [claude, "plugin", "install", o.get("id", ""), "-s", "user"],
                       "plugin-enable": [claude, "plugin", "enable", o.get("id", ""), "-s", "user"]}[o["op"]]
                r = self.ctx.capture(cmd, timeout=180)
                good = r is not None and r.returncode == 0
                msg = "" if r is None else (r.stderr or r.stdout).strip().splitlines()[-1:]
                self.ctx.say(f"      claude-code: {' '.join(cmd[2:])} {'ok' if good else 'FAILED: ' + ' '.join(msg)}")
                ok &= good
            elif o["op"] == "claude-md":
                p = self.ctx.home / ".claude/CLAUDE.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                old = p.read_text() if p.is_file() else ""
                tmp = p.with_suffix(".md.tmp")
                tmp.write_text(with_claude_md_imports(old, o["paths"]))
                os.replace(tmp, p)
                self.ctx.say(f"      {p}: {len(o['paths'])} import(s)")
            elif o["op"] == "opencode-instr":
                oc_instr = o["list"]
            elif o["op"] == "unlink":
                p = Path(o["path"])
                if p.is_symlink():
                    p.unlink()
                    self.ctx.say(f"      removed {p}")
        if oc_items or oc_instr is not None:
            good, msg = self._opencode(oc_items, oc_instr)
            what = [n for n, _ in oc_items] + (["instructions"] if oc_instr is not None else [])
            self.ctx.say(f"      opencode: {', '.join(what)} {'ok' if good else 'FAILED: ' + msg}")
            ok &= good
        return ok
