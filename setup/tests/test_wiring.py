"""wiring + secrets: plan logic with fake facts, the JSONC reader, and the real registry parsing."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from agentsetup import tomlmini  # noqa: E402
from agentsetup.components.wiring import Wiring, strip_jsonc, has_comments  # noqa: E402
from agentsetup.core import CHANGE, OK, Context  # noqa: E402

REPO = HERE.parent.parent


def wiring(machine="aibo-linux", os_name="linux"):
    c = Context(REPO, {"machine": {"name": machine}}, os_name=os_name, home="/home/x", interactive=False)
    return Wiring(c, {})


HTTP = {"type": "http", "url": "http://127.0.0.1:8009/mcp"}


def facts(cur_mcp, links=None, bins=None):
    return {"programs": {"claude-code": {"installed": True, "mcp_kind": "claude-cli", "mcp": cur_mcp,
                                         "skills_dir": "/home/x/.claude/skills", "skills": links or {}}},
            "want_mcp": {"agent-tools": dict(HTTP, programs=None)}, "held": [],
            "want_skills": {"memory": {"path": "/r/memory/skill", "programs": None}},
            "want_bin": {"fleet-secret": "/r/bin/fleet-secret"}, "bin": bins or {"fleet-secret": "/r/bin/fleet-secret"},
            "problems": [], "managed_roots": ["/r"]}


class WiringPlan(unittest.TestCase):
    def test_in_place(self):
        p = wiring().plan(facts({"agent-tools": dict(HTTP)}, {"memory": "/r/memory/skill"}))
        self.assertEqual(p.status, OK)

    def test_adds_missing_and_updates_changed(self):
        p = wiring().plan(facts({"agent-tools": {"type": "http", "url": "http://old"}}, {}))
        self.assertEqual(p.status, CHANGE)
        self.assertIn("claude-code: update MCP server agent-tools", p.actions)
        self.assertIn("claude-code: link skill memory", p.actions)

    def test_unknown_servers_reported_not_removed(self):
        p = wiring().plan(facts({"agent-tools": dict(HTTP), "hand-added": dict(HTTP)}, {"memory": "/r/memory/skill"}))
        self.assertEqual(p.status, OK)
        self.assertTrue(any("hand-added" in n for n in p.notes))

    def test_stale_managed_link_removed_foreign_kept(self):
        links = {"memory": "/r/memory/skill", "old": "/r/skills/old", "vendor": "/opt/v/skill", "real": "<dir>"}
        p = wiring().plan(facts({"agent-tools": dict(HTTP)}, links))
        self.assertEqual(p.actions, ["claude-code: remove stale skill link old"])

    def test_real_file_in_bin_left_alone(self):
        p = wiring().plan(facts({"agent-tools": dict(HTTP)}, {"memory": "/r/memory/skill"}, {"fleet-secret": "<file>"}))
        self.assertEqual(p.status, OK)
        self.assertTrue(any("real file" in n for n in p.notes))


class PluginPlan(unittest.TestCase):
    WANT = {"mp": {"id": "mp-skills@mp", "marketplace": "mp/skills"}}

    def plan(self, plugins):
        f = facts({"agent-tools": dict(HTTP)}, {"memory": "/r/memory/skill"})
        f["want_plugins"], f["plugins"] = self.WANT, plugins
        return wiring().plan(f)

    def test_missing_plugin_adds_marketplace_and_installs(self):
        p = self.plan({"installed": [], "enabled": {}, "repos": []})
        self.assertEqual(p.actions, ["claude-code: add plugin marketplace mp/skills", "claude-code: install plugin mp-skills@mp"])

    def test_installed_plugin_in_place(self):
        p = self.plan({"installed": ["mp-skills@mp"], "enabled": {"mp-skills@mp": True}, "repos": ["mp/skills"]})
        self.assertEqual(p.status, OK)

    def test_disabled_plugin_enabled(self):
        p = self.plan({"installed": ["mp-skills@mp"], "enabled": {"mp-skills@mp": False}, "repos": ["mp/skills"]})
        self.assertEqual(p.actions, ["claude-code: enable plugin mp-skills@mp"])

    def test_unlisted_plugin_reported_not_removed(self):
        p = self.plan({"installed": ["mp-skills@mp", "other@x"], "enabled": {}, "repos": ["mp/skills"]})
        self.assertEqual(p.status, OK)
        self.assertTrue(any("other@x" in n for n in p.notes))


class Jsonc(unittest.TestCase):
    def test_strip(self):
        t = '{\n // c\n "u": "http://x//y", /* b */ "a": [1,2,],\n}'
        self.assertEqual(__import__("json").loads(strip_jsonc(t)), {"u": "http://x//y", "a": [1, 2]})
        self.assertTrue(has_comments(t))
        self.assertFalse(has_comments('{"u": "http://x//y"}'))


class Registry(unittest.TestCase):
    def test_parses_with_the_mini_parser_too(self):
        text = (REPO / "registry.toml").read_text()
        for force in (False, True):
            d = tomlmini.loads(text, force_mini=force)
            self.assertIn("agent-tools", d["mcp"])
            self.assertNotIn("fleet", d["mcp"])          # Chief-only (D6)
            self.assertFalse(d["mcp"]["agent-browser-mac"]["enabled"])

    def test_no_secrets_in_registry(self):
        self.assertNotRegex((REPO / "registry.toml").read_text(), r"sk-or-|tk_[A-Za-z0-9]{8}")


class FleetSecret(unittest.TestCase):
    def run_fs(self, f, *args, stdin=None):
        env = dict(os.environ, FLEET_SECRETS_FILE=f)
        return subprocess.run([str(REPO / "bin/fleet-secret"), *args], capture_output=True, text=True, env=env, input=stdin)

    def test_roundtrip_and_render(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "k.env")
            Path(f).write_text('# c\nA=1\nexport B="x y"\n')
            self.assertEqual(self.run_fs(f, "get", "B").stdout, "x y")
            self.run_fs(f, "set", "Q", 'say "hi" \\ #')
            self.assertEqual(self.run_fs(f, "get", "Q").stdout, 'say "hi" \\ #')
            self.assertEqual(oct(os.stat(f).st_mode & 0o777), "0o600")
            Path(d, "t").write_text("X=${A}/${B}\n# ${…} stays\n")
            self.assertEqual(self.run_fs(f, "render", os.path.join(d, "t"), os.path.join(d, "o")).returncode, 0)
            self.assertEqual(Path(d, "o").read_text(), "X=1/x y\n# ${…} stays\n")
            Path(d, "t2").write_text("X=${NOPE}\n")
            self.assertNotEqual(self.run_fs(f, "render", os.path.join(d, "t2"), os.path.join(d, "o2")).returncode, 0)
            self.assertNotEqual(self.run_fs(f, "set", "M", stdin="a\nb").returncode, 0)


if __name__ == "__main__":
    unittest.main()
