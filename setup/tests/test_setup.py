"""Unit tests for agent-tools setup: component planning with fake detect results, profiles, the TOML
reader, redaction, snapshots and the apply/rollback flow. No test touches launchd, systemd or the network.

    python3 setup/tests/test_setup.py        (from the agent-tools folder; Python 3.9+)
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from agentsetup import cli, core, profile, tomlmini  # noqa: E402
from agentsetup.components import ALL, BY_NAME  # noqa: E402
from agentsetup.core import CHANGE, ERROR, GAP, OK, SKIP, Context, Plan  # noqa: E402

REPO = HERE.parents[1]
HOME = "/home/tester"


def ctx(os_name="darwin", prof=None, home=HOME, **kw):
    c = Context(REPO, prof or {}, os_name=os_name, home=home, interactive=False, **kw)
    c._secrets = {}
    return c


def comp(name, os_name="darwin", cfg=None, prof=None):
    return BY_NAME[name](ctx(os_name, prof), cfg or {})


def job(label, loaded=True, named=None, env=None, runs="python3.12 /x/job.py", keepalive=True, state="running"):
    return {"label": label, "plist": f"{HOME}/Library/LaunchAgents/{label}.plist", "loaded": loaded,
            "state": state if loaded else None, "pid": "1", "program": "python3.12", "runs": runs,
            "env": env or {}, "env_keys": sorted(env or {}), "keepalive": keepalive, "named": named}


def named(name, kind="pa-app", bundle="com.example.x", probe_ok=False):
    return {"name": name, "bundle_id": bundle, "kind": kind, "probe_ok": probe_ok,
            "exe": f"{HOME}/Applications/Apps/{name}.app/Contents/MacOS/{name}", "app": f"{HOME}/Applications/Apps/{name}.app"}


# ------------------------------------------------------------------------------------ planning
class ComputerUsePlan(unittest.TestCase):
    full = {"cua": "0.30.2", "daemon": True, "agent_browser": "0.38.1", "claude": True, "claude_mcp": True,
            "opencode": True, "opencode_mcp": True, "agent_chrome": True, "cdp": True}

    def test_all_in_place(self):
        p = comp("computer-use", cfg={"agent_chrome": True}).plan(self.full)
        self.assertEqual(p.status, OK)
        self.assertIn("cua-driver 0.30.2", p.summary)

    def test_missing_agent_chrome_runs_installer_with_flag(self):
        p = comp("computer-use", cfg={"agent_chrome": True}).plan(dict(self.full, agent_chrome=False, cdp=False))
        self.assertEqual(p.status, CHANGE)
        self.assertIn("--agent-chrome", p.actions[0])
        self.assertTrue(any("restarts the cua-driver daemon" in n for n in p.notes))

    def test_chrome_not_wanted_is_left_alone(self):
        p = comp("computer-use", cfg={"agent_chrome": False}).plan(dict(self.full, agent_chrome=True))
        self.assertEqual(p.status, OK)
        self.assertTrue(p.notes)

    def test_fresh_mac_needs_permission_grant(self):
        f = dict.fromkeys(self.full, False)
        f.update(cua=None, agent_browser=None)
        p = comp("computer-use").plan(f)
        self.assertEqual(p.status, CHANGE)
        self.assertTrue(any("permissions grant" in h for h in p.human))

    def test_linux_not_applicable(self):
        self.assertIn("Linux", comp("computer-use", "linux").not_applicable())


class LaunchAgentPlans(unittest.TestCase):
    def test_stuck_watch_install_load_ok(self):
        c = comp("stuck-watch")
        self.assertEqual(c.plan({"jobs": []}).data["do"], "install")
        p = c.plan({"jobs": [job("com.example.stuck-watch", loaded=False)]})
        self.assertEqual((p.status, p.data["do"]), (CHANGE, "load"))
        p = c.plan({"jobs": [job("com.x.stuck-watch", named=named("Stuck Watch"))]})
        self.assertEqual(p.status, OK)
        self.assertIn('"Stuck Watch"', p.summary)

    def test_duplicate_jobs_noted(self):
        p = comp("stuck-watch").plan({"jobs": [job("a"), job("b")]})
        self.assertEqual(p.status, OK)
        self.assertIn("more than one", p.notes[0])

    def test_scheduled_job_wording(self):
        p = comp("sync-ai-sessions").plan({"jobs": [job("s", keepalive=False, state="not running")], "repo": "x", "uv": True})
        self.assertIn("scheduled", p.summary)

    def test_sync_needs_repo_and_uv(self):
        p = comp("sync-ai-sessions").plan({"jobs": [], "repo": None, "uv": False})
        self.assertEqual(p.status, GAP)
        self.assertEqual(len(p.human), 2)
        p = comp("sync-ai-sessions").plan({"jobs": [], "repo": "Documents/ai-memory", "uv": True})
        self.assertEqual(p.status, CHANGE)

    def test_sync_linux(self):
        st = {"enabled": "not-found", "active": "inactive"}
        self.assertEqual(comp("sync-ai-sessions", "linux").plan({"timer": st}).status, GAP)
        p = comp("sync-ai-sessions", "linux", {"installer": "/x/setup-sync.sh"}).plan({"timer": st})
        self.assertEqual(p.status, CHANGE)
        self.assertEqual(comp("sync-ai-sessions", "linux").plan({"timer": {"enabled": "enabled", "active": "active"}}).status, OK)


class WatchdogPlan(unittest.TestCase):
    def test_mac_install_with_custom_target_sets_env(self):
        p = comp("cptr-watchdog").plan({"jobs": [], "target_label": "com.example.cptr", "target_port": 7777})
        self.assertEqual(p.status, CHANGE)
        self.assertTrue(p.data["set_env"])
        self.assertIn("CPTR_WATCHDOG_PORT=7777", p.actions[1])

    def test_mac_default_target_no_env(self):
        p = comp("cptr-watchdog").plan({"jobs": [], "target_label": "com.cptr.run", "target_port": 8000})
        self.assertFalse(p.data["set_env"])
        self.assertEqual(len(p.actions), 1)

    def test_mac_mismatch_fixes_env_only(self):
        f = {"jobs": [job("com.cptr.watchdog")], "target_label": "com.example.cptr", "target_port": 7777}
        p = comp("cptr-watchdog").plan(f)
        self.assertEqual((p.status, p.data["do"]), (CHANGE, "fix"))
        self.assertIn("cptr isn't restarted", p.actions[0])

    def test_mac_missing_cptr_job_blocks(self):
        f = {"jobs": [job("w")], "target_label": "com.example.cptr", "target_port": 7777, "cptr_jobs": ["com.cptr.run"]}
        self.assertEqual(comp("cptr-watchdog").plan(f).status, GAP)

    def test_mac_matching(self):
        f = {"jobs": [job("w", env={"CPTR_WATCHDOG_LABEL": "com.example.cptr", "CPTR_WATCHDOG_PORT": "7777"})],
             "target_label": "com.example.cptr", "target_port": 7777}
        self.assertEqual(comp("cptr-watchdog").plan(f).status, OK)

    def test_linux(self):
        c = comp("cptr-watchdog", "linux")
        off = {"enabled": "not-found", "active": "inactive"}
        on = {"enabled": "enabled", "active": "active"}
        p = c.plan({"timer": off, "service_file": False, "port": None, "target_port": 8899})
        self.assertEqual((p.status, p.data["do"], p.data["dropin"]), (CHANGE, "install", False))
        p = c.plan({"timer": off, "service_file": False, "port": None, "target_port": 8000})
        self.assertTrue(p.data["dropin"])
        p = c.plan({"timer": on, "service_file": True, "port": "8000", "target_port": 8000})
        self.assertEqual(p.status, OK)
        p = c.plan({"timer": on, "service_file": True, "port": None, "target_port": 8000})
        self.assertEqual((p.status, p.data["do"]), (CHANGE, "fix"))


class McpToolsPlan(unittest.TestCase):
    base = {"claude_wired": True, "opencode": False, "opencode_wired": False}

    def test_docker_mode(self):
        c = comp("mcp-tools", "linux", {"mode": "docker"})
        self.assertEqual(c.plan(dict(self.base, answering=True)).status, OK)
        self.assertEqual(c.plan(dict(self.base, answering=False)).status, GAP)

    def test_mac_needs_secrets(self):
        p = comp("mcp-tools").plan(dict(self.base, answering=False, jobs=[], secrets=False))
        self.assertEqual(p.status, GAP)
        p = comp("mcp-tools").plan(dict(self.base, answering=False, jobs=[], secrets=True))
        self.assertEqual((p.status, p.data["do"]), (CHANGE, "install"))

    def test_mac_loaded_not_answering(self):
        p = comp("mcp-tools").plan(dict(self.base, answering=False, jobs=[job("com.example.mcp-tools")], secrets=True))
        self.assertEqual(p.status, GAP)

    def test_unwired_claude_is_a_note(self):
        p = comp("mcp-tools").plan(dict(self.base, claude_wired=False, answering=True, jobs=[job("m")], secrets=True))
        self.assertEqual(p.status, OK)
        self.assertIn("claude mcp add", p.notes[0])


class CptrPlans(unittest.TestCase):
    def f(self, **kw):
        d = {"label": "com.cptr.run", "port": 8000, "how": "profile", "service": "cptr", "found": ["com.cptr.run"],
             "loaded": True, "named": None, "healthy": True, "version": "0.9.21+fork", "install": "uv"}
        d.update(kw)
        return d

    def test_cptr_report(self):
        self.assertEqual(comp("cptr").plan(self.f()).status, OK)
        self.assertEqual(comp("cptr").plan(self.f(healthy=False)).status, GAP)
        self.assertEqual(comp("cptr").plan(self.f(found=[])).status, GAP)
        p = comp("cptr").plan(self.f(label="com.example.cptr"))
        self.assertIn("profile says", p.summary)
        p = comp("cptr").plan(self.f(label=None, found=["a", "b"]))
        self.assertIn("more than one", p.summary)

    def test_cptr_never_changes(self):
        for kw in ({}, {"healthy": False}, {"found": []}):
            self.assertNotEqual(comp("cptr").plan(self.f(**kw)).status, CHANGE)

    def test_cptr_app(self):
        c = comp("cptr-app")
        self.assertTrue(c.restarts_cptr)
        base = {"label": "com.cptr.run", "port": 8000, "job": True, "named": None, "fda": None, "probe": None}
        p = c.plan(base)
        self.assertEqual(p.status, CHANGE)
        self.assertIn("Full Disk Access", p.actions[1])
        self.assertIn("DETACHED", p.actions[2])
        self.assertIn("health gate", p.actions[3])
        self.assertTrue(p.human)
        n = named("cptr", bundle="com.example.cptr")
        self.assertEqual(c.plan(dict(base, named=n, fda=True)).status, OK)
        self.assertEqual(c.plan(dict(base, named=n, fda=False)).status, GAP)
        self.assertEqual(c.plan(dict(base, named=n, fda=False, probe=True)).status, OK)
        self.assertEqual(c.plan(dict(base, named=n, fda=True, probe=False)).status, GAP)
        unknown = c.plan(dict(base, named=n))
        self.assertEqual(unknown.status, OK)
        self.assertTrue(unknown.notes)
        self.assertEqual(c.plan(dict(base, job=False)).status, GAP)
        self.assertIn("Linux", comp("cptr-app", "linux").not_applicable())


class NamedAppsPlan(unittest.TestCase):
    def test_mixed(self):
        c = comp("named-apps", cfg={"apps": ["stuck-watch", "cptr-watchdog", "sync-ai-sessions"]})
        f = {"jobs": {"stuck-watch": [job("s", named=named("Stuck Watch"))], "cptr-watchdog": [job("com.cptr.watchdog")],
                      "sync-ai-sessions": []}}
        p = c.plan(f)
        self.assertEqual(p.status, CHANGE)
        self.assertEqual(p.data["kinds"], ["watchdog"])
        self.assertIn("sync-ai-sessions", p.notes[0])

    def test_all_named(self):
        c = comp("named-apps")
        f = {"jobs": {"stuck-watch": [job("s", named=named("Stuck Watch"))], "cptr-watchdog": [job("w", named=named("cptr Watchdog"))]}}
        self.assertEqual(c.plan(f).status, OK)

    def test_unknown_app(self):
        with self.assertRaises(ValueError):
            comp("named-apps", cfg={"apps": ["nope"]}).apps()


class Evaluate(unittest.TestCase):
    def test_check_only_turns_change_into_gap(self):
        c = comp("stuck-watch", cfg={"check_only": True})
        c.detect = lambda: {"jobs": []}
        p = cli.evaluate(c)
        self.assertEqual(p.status, GAP)
        self.assertTrue(any("check_only" in h for h in p.human))

    def test_detect_error_is_contained(self):
        c = comp("stuck-watch")

        def boom():
            raise RuntimeError("launchctl exploded")
        c.detect = boom
        c.ctx.log = lambda m: None
        self.assertEqual(cli.evaluate(c).status, ERROR)

    def test_platform_skip(self):
        self.assertEqual(cli.evaluate(comp("named-apps", "linux")).status, SKIP)

    def test_registry_order(self):
        names = [c.name for c in ALL]
        self.assertEqual(names[-1], "cptr-app")
        self.assertLess(names.index("stuck-watch"), names.index("named-apps"))
        self.assertLess(names.index("cptr-watchdog"), names.index("named-apps"))


class LinuxDetect(unittest.TestCase):
    """Linux detection logic with systemctl and HTTP faked (no Linux box needed)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        units = self.home / ".config/systemd/user"
        (units / "cptr-watchdog.service.d").mkdir(parents=True)
        (units / "cptr-watchdog.service").write_text("[Service]\nExecStart=/x/watchdog.sh\n")
        (units / "cptr-watchdog.service.d/agent-setup.conf").write_text("[Service]\nEnvironment=CPTR_WATCHDOG_PORT=8000\n")
        self.state = {"cptr-watchdog.timer": ("enabled", "active"), "cptr": ("enabled", "active"),
                      "sync-ai-sessions.timer": ("not-found", "inactive")}
        self.prof = {"cptr": {"port": 8000, "install": "uv"}}
        self.c = ctx("linux", self.prof, home=self.home)

        def capture(cmd, timeout=20, env=None, cwd=None):
            if cmd[:2] == ["systemctl", "--user"] and cmd[2] in ("is-enabled", "is-active"):
                en, ac = self.state.get(cmd[3], ("not-found", "inactive"))
                out = en if cmd[2] == "is-enabled" else ac
                ok = out in ("enabled", "active")
                return core.subprocess.CompletedProcess(cmd, 0 if ok else 1, out + "\n", "")
            return core.subprocess.CompletedProcess(cmd, 127, "", "not faked")
        self.c.capture = capture
        from agentsetup import host
        self.host = host
        self.saved = host.http_status, host.cptr_healthy
        host.http_status = lambda url, timeout=4: 200
        host.cptr_healthy = lambda port: True

    def tearDown(self):
        self.host.http_status, self.host.cptr_healthy = self.saved
        self.tmp.cleanup()

    def plan(self, name, cfg=None):
        return cli.evaluate(BY_NAME[name](self.c, cfg or {}))

    def test_watchdog_reads_dropin_port(self):
        p = self.plan("cptr-watchdog")
        self.assertEqual(p.status, OK, p.summary)
        self.assertIn(":8000", p.summary)

    def test_sync_timer_missing(self):
        self.assertEqual(self.plan("sync-ai-sessions").status, GAP)
        self.assertEqual(self.plan("sync-ai-sessions", {"installer": "bash {profile_dir}/x.sh"}).status, CHANGE)

    def test_cptr_unit(self):
        p = self.plan("cptr")
        self.assertEqual(p.status, OK, p.summary)
        self.assertIn("cptr.service", p.summary)
        self.state["cptr"] = ("not-found", "inactive")
        self.assertEqual(self.plan("cptr").status, GAP)

    def test_mcp_docker(self):
        self.assertEqual(self.plan("mcp-tools", {"mode": "docker"}).status, OK)

    def test_mac_only_components_skip(self):
        for name in ("computer-use", "stuck-watch", "named-apps", "cptr-app"):
            self.assertEqual(self.plan(name).status, SKIP, name)

    def test_paths(self):
        self.assertTrue(str(self.c.log_path).endswith(".local/state/agent-setup/agent-setup.log"))
        self.c.profile["_path"] = "/srv/infra/machines/box/setup.toml"
        self.assertEqual(self.c.expand("{profile_dir}/../x"), "/srv/infra/machines/box/../x")
        self.assertEqual(self.c.expand("{agent_tools}/.env"), f"{REPO}/.env")


# ------------------------------------------------------------------------------- apply / rollback
class FakeComponent(core.Component):
    name = "fake"
    order = 1

    def __init__(self, ctx, cfg=None, verify_ok=True, file=None, restarts=False):
        super().__init__(ctx, cfg)
        self.verify_ok, self.file, self.restarts_cptr = verify_ok, file, restarts
        self.applied = self.rolled_back = False

    def detect(self):
        return {"done": self.applied and self.verify_ok}

    def plan(self, f):
        return Plan(OK, "done") if f["done"] else Plan(CHANGE, "todo", actions=["write the file"])

    def owned_files(self):
        return [self.file] if self.file else []

    def apply(self, plan):
        self.applied = True
        if self.file:
            Path(self.file).write_text("new")
        return True

    def rollback(self):
        self.rolled_back = True
        return core.restore_snapshot(self.ctx, self.name)


class ApplyFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.ctx = ctx(home=self.home)
        self.args = cli.parse(["--yes"])
        self.out = cli.Out(self.ctx, False)

    def tearDown(self):
        self.tmp.cleanup()

    def run_apply(self, comps):
        plans = {c.name: cli.evaluate(c) for c in comps}
        with redirect_stdout(io.StringIO()):
            return cli.apply_all(self.ctx, self.out, comps, plans, "test", self.args)

    def test_verified_apply(self):
        c = FakeComponent(self.ctx)
        self.assertEqual(self.run_apply([c]), 0)
        self.assertTrue(c.applied)
        self.assertFalse(c.rolled_back)

    def test_failed_verify_rolls_back_snapshot(self):
        f = self.home / "owned.conf"
        f.write_text("old")
        c = FakeComponent(self.ctx, verify_ok=False, file=str(f))
        self.assertEqual(self.run_apply([c]), 1)
        self.assertTrue(c.rolled_back)
        self.assertEqual(f.read_text(), "old")

    def test_rollback_deletes_created_file(self):
        f = self.home / "created.conf"
        c = FakeComponent(self.ctx, verify_ok=False, file=str(f))
        self.run_apply([c])
        self.assertFalse(f.exists())

    def test_cptr_restart_needs_person(self):
        self.args = cli.parse([])                 # no --yes, not interactive
        c = FakeComponent(self.ctx, restarts=True)
        self.assertEqual(self.run_apply([c]), 3)
        self.assertFalse(c.applied)


# ---------------------------------------------------------------------------------- profiles
class Profiles(unittest.TestCase):
    facts = {"os": "darwin", "hostname": "Mac.example.net", "local_hostname": "box-a",
             "computer_name": "Sam’s Mac mini", "model": "Mac14,12"}

    def test_matching(self):
        m = profile.matches
        self.assertTrue(m({"computer_name": "Sam's Mac mini"}, self.facts))       # curly vs straight
        self.assertTrue(m({"hostname": "mac"}, self.facts))                       # short, any case
        self.assertTrue(m({"model": ["Mac16,10", "Mac14,12"]}, self.facts))
        self.assertFalse(m({"local_hostname": "box-a", "model": "Mac16,10"}, self.facts))   # all must match
        self.assertFalse(m({}, self.facts))

    def test_pick_for_host(self):
        with tempfile.TemporaryDirectory() as d:
            for name, match in (("box-a", 'local_hostname = "box-a"\nmodel = "Mac14,12"'),
                                ("box-b", 'local_hostname = "box-b"'), ("linux", 'os = "linux"')):
                (Path(d) / "machines" / name).mkdir(parents=True)
                (Path(d) / "machines" / name / "setup.toml").write_text(f"[match]\n{match}\n")
            self.assertEqual(profile.pick_for_host(d, self.facts).parent.name, "box-a")
            with self.assertRaises(profile.ProfileError):
                profile.pick_for_host(d, dict(self.facts, local_hostname="other"))

    def test_locate_order(self):
        with tempfile.TemporaryDirectory() as d:
            default = Path(d) / ".config/agent-setup/profile.toml"
            default.parent.mkdir(parents=True)
            default.write_text("")
            self.assertEqual(profile.locate("/x.toml", {"AGENT_SETUP_PROFILE": "/e.toml"}, d)[0], Path("/x.toml"))
            self.assertEqual(profile.locate(None, {"AGENT_SETUP_PROFILE": "/e.toml"}, d)[0], Path("/e.toml"))
            self.assertEqual(profile.locate(None, {}, d)[0], default)
            self.assertIsNone(profile.locate(None, {}, d + "/nowhere")[0])

    def test_select_enabled_and_only(self):
        prof = {"components": {"stuck-watch": {"enabled": True}, "cptr": {"enabled": False}, "named-apps": {}}}
        self.assertEqual([c.name for c, _ in cli.select(prof, None)], ["stuck-watch", "named-apps"])
        self.assertEqual([c.name for c, _ in cli.select(prof, "cptr")], ["cptr"])
        with self.assertRaises(SystemExit):
            cli.select(prof, "nope")


class Toml(unittest.TestCase):
    sample = '''
# comment
[machine]
name = "box"   # trailing
[match]
computer_name = "Sam\\u2019s Mac"
model = ['Mac14,12', "Mac16,10"]
[components.cptr-app]
enabled = true
port = 8_000
ratio = 1.5
apps = [
  "a",  # first
  "b",
]
inline = { x = 1, y = "z" }
'''

    def test_mini_parser(self):
        d = tomlmini.loads(self.sample, force_mini=True)
        self.assertEqual(d["match"]["computer_name"], "Sam’s Mac")
        self.assertEqual(d["components"]["cptr-app"], {"enabled": True, "port": 8000, "ratio": 1.5,
                                                      "apps": ["a", "b"], "inline": {"x": 1, "y": "z"}})

    def test_mini_matches_tomllib_on_shipped_profiles(self):
        files = [REPO / "setup/profiles/example.toml"]
        if os.environ.get("AGENT_SETUP_PROFILES_DIR"):      # also check a private profiles folder
            files += sorted(Path(os.environ["AGENT_SETUP_PROFILES_DIR"]).expanduser().glob("*/setup.toml"))
        for f in files:
            mini = tomlmini.loads(f.read_text(), force_mini=True)
            if tomlmini._tomllib:
                self.assertEqual(mini, tomlmini.loads(f.read_text()), f)
            self.assertIn("components", mini, f)

    def test_errors(self):
        for bad in ('a = ', '[x\n', 'a = "open', 'a = 1\na = 2'):
            with self.assertRaises(tomlmini.TOMLError):
                tomlmini.loads(bad, force_mini=True)


class Redaction(unittest.TestCase):
    def test_patterns(self):
        r = core.redact
        self.assertNotIn("abc123secret", r("NTFY_TOKEN=abc123secret"))
        self.assertNotIn("tk_abcdefghijk", r("using tk_abcdefghijk now"))
        self.assertNotIn("hunter22", r("https://user:hunter22@host/x"))
        self.assertNotIn("eyJhbGciOi", r("Authorization: Bearer eyJhbGciOi.x"))
        self.assertEqual(r("port 8000 is fine"), "port 8000 is fine")
        self.assertNotIn("plainvalue99", r("got plainvalue99", ["plainvalue99"]))

    def test_env_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write('# c\nexport A="x y"\nB=plain # note\nC=\'q\'\nnot a line\n')
        try:
            self.assertEqual(core.read_env_file(f.name), {"A": "x y", "B": "plain", "C": "q"})
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main(verbosity=1)
