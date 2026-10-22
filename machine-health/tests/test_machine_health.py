import json, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import docker_hygiene as dh
import mem_reaper as mr
import idle_simulators as isim
import disk_usage as du
import stale_prompts as sp
import mem_guard as mg
from unittest import mock


def ev(msg, ts, pid=100):
    return json.dumps({"eventMessage": msg, "timestamp": ts, "processID": pid})


class StalePrompts(unittest.TestCase):
    def lines(self, granted):
        L = [ev("AUTHREQ_CTX: msgID=1.1, service=kTCCServiceScreenCapture, preflight=no", "2026-10-21 10:04:00.000000+0000"),
             ev("AUTHREQ_ATTRIBUTION: msgID=1.1, accessing={TCCDProcess: identifier=com.cos.node, pid=5}, responsible={TCCDProcess: identifier=com.cos.node, pid=5}", "2026-10-21 10:04:00.100000+0000"),
             ev("AUTHREQ_PROMPTING: msgID=1.1, service=kTCCServiceScreenCapture, Resp:{TCCDProcess: identifier=com.cos.node, pid=5}", "2026-10-21 10:04:01.000000+0000")]
        if granted:
            L += [ev("AUTHREQ_CTX: msgID=2.2, service=kTCCServiceScreenCapture, preflight=yes", "2026-10-21 12:00:00.000000+0000"),
                  ev("AUTHREQ_ATTRIBUTION: msgID=2.2, accessing={TCCDProcess: identifier=com.cos.node, pid=9}", "2026-10-21 12:00:00.100000+0000"),
                  ev("AUTHREQ_RESULT: msgID=2.2, authValue=2, authReason=4", "2026-10-21 12:00:01.000000+0000")]
        return L

    def test_stale_when_granted_after_prompt(self):
        prompts, grants = sp.parse_events(self.lines(True))
        started = prompts[0]["t"] + 2
        self.assertEqual(sp.decide(started, prompts, grants)[0], "stale")

    def test_kept_when_no_grant_seen(self):
        prompts, grants = sp.parse_events(self.lines(False))
        self.assertEqual(sp.decide(prompts[0]["t"], prompts, grants)[0], "unknown")

    def test_unknown_when_no_matching_prompt(self):
        prompts, grants = sp.parse_events(self.lines(True))
        self.assertEqual(sp.decide(prompts[0]["t"] + 99999, prompts, grants)[0], "unknown")

    def test_etime(self):
        self.assertEqual(sp.etime_s("07:01:02"), 25262)
        self.assertEqual(sp.etime_s("1-00:00:05"), 86405)


class DockerHygiene(unittest.TestCase):
    def img(self, repo, tag, id, created, size=1e8):
        return {"repo": repo, "tag": tag, "id": id, "size": size, "created": created}

    def test_never_lists_rollback_or_recent_hub(self):
        imgs = [self.img("lifeos-rollback", "pre-x", "a", "1"), self.img("lifeos-web-rollback", "pre-y", "b", "1"),
                self.img("cos-hub", "latest", "h5", "5"), self.img("cos-hub", "previous", "h4", "4"),
                self.img("cos-hub", "h3", "h3", "3"), self.img("cos-hub", "h2", "h2", "2"),
                self.img("cos-hub", "h1", "h1", "1"), self.img("cos-hub", "rollback-1011", "r", "0"),
                self.img("cosreltest9-hub", "x", "t", "1"), self.img("lifeos-web", "final", "w", "1"),
                self.img("lifeos-web", "latest", "w", "1"), self.img("<none>", "<none>", "d", "1")]
        c = dh.classify(imgs)
        flat = {(i["repo"], i["tag"]) for v in c.values() for i in v}
        self.assertFalse(any("rollback" in r + t for r, t in flat))
        self.assertNotIn(("cos-hub", "latest"), flat)
        self.assertIn(("cosreltest9-hub", "x"), flat)
        self.assertIn(("lifeos-web", "final"), flat)
        self.assertNotIn(("lifeos-web", "latest"), flat)
        self.assertEqual({i["tag"] for i in c["cos-hub beyond newest 3"]}, {"h1", "h2"})
        self.assertEqual(len(c["dangling"]), 1)

    def test_module_never_deletes(self):
        self.assertNotRegex(open(dh.__file__).read(), r"\"(rmi|rm|prune|image)\"")


class MemReaper(unittest.TestCase):
    def test_keep_tab_pattern(self):
        for u in ("https://x.com/login", "https://accounts.google.com/x", "about:blank", "https://a.b/sso/start"):
            self.assertTrue(mr.KEEP_TAB.search(u), u)
        self.assertFalse(mr.KEEP_TAB.search("http://127.0.0.1:4000/viewer"))

    def test_etime(self):
        self.assertEqual(mr.etime_s("10-18:42:28"), 10 * 86400 + 18 * 3600 + 42 * 60 + 28)


class IdleSimulators(unittest.TestCase):
    U = "AAAAAAAA-1111-2222-3333-444444444444"
    PS = (f" 10 2-05:00:00 /x/launchd_sim /Users/a/Library/Developer/CoreSimulator/Devices/{U}/data/var/run/launchd_sim_socket\n"
          " 11 00:05 /usr/bin/python3 foo.py\n")

    def test_shutdown_when_old_and_idle(self):
        sims, active = isim.parse(self.PS)
        self.assertEqual(isim.decide(sims, active, 7200)[0], "shutdown")

    def test_kept_when_build_active(self):
        sims, active = isim.parse(self.PS + " 12 00:10 /usr/bin/xcodebuild -scheme cos test\n")
        self.assertEqual(isim.decide(sims, active, 7200)[0], "none")

    def test_kept_when_young_or_none(self):
        sims, active = isim.parse(self.PS.replace("2-05:00:00", "00:30:00"))
        self.assertEqual(isim.decide(sims, active, 7200)[0], "none")
        self.assertEqual(isim.decide({}, [], 7200)[0], "none")


class DiskUsage(unittest.TestCase):
    def test_warn_threshold(self):
        self.assertTrue(du.decide_warn(9.9, 15))
        self.assertFalse(du.decide_warn(60, 15))

    def test_free_pct_sane(self):
        pct, free, total = du.free_pct()
        self.assertTrue(0 < pct <= 100 and free < total)


class MemGuard(unittest.TestCase):
    def P(self):
        mk = lambda ppid, age, args: dict(ppid=ppid, age=age, rss=1000, args=args)
        return {
            10: mk(1, 5000, "python3 /Users/x/.local/bin/claude --output-format stream-json"),              # a running agent: never
            11: mk(1, 5000, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),                 # his Chrome: never
            12: mk(1, 5000, "/x/colima/limactl hostagent"),                                                   # colima: never
            20: mk(1, 900, "/c/ms-playwright/chromium-1/Chromium --headless"),                                # orphan: yes
            21: mk(77, 900, "/c/ms-playwright/chromium-1/Chromium --headless"),                               # live parent: no
            22: mk(1, 30, "/c/ms-playwright/chromium-1/Chromium --headless"),                                 # too young: no
            30: mk(1, 4000, "python3 /w/cos-wt-a/scripts/dev_hub.py --port 4390"),                           # old dev hub: yes
            31: mk(1, 100, "python3 /w/cos-wt-a/scripts/fake_node.py"),                                       # young: no
            40: mk(1, 5000, "python3 /w/cos-wt-b/scripts/dev_hub.py --port 4391"),                           # holder's child: spared
            41: mk(900, 5000, "xcodebuild test"),
            900: mk(1, 5000, "bash scripts/test.sh --strict"),                                                # lock holder
        }
        
    def run_clear(self, holder=0):
        with mock.patch.object(mg, "lock_holder", return_value=holder), mock.patch.object(mg.sw, "log"), \
             mock.patch.object(mg.mr, "session_files", return_value={}):
            P = self.P()
            if holder:
                P[40]["ppid"] = holder
            return mg.clear(P, dry=True)

    def test_kills_only_orphans_and_old_hubs(self):
        done = " | ".join(self.run_clear())
        self.assertIn("Playwright browser", done); self.assertIn("dev_hub.py --port 4390", done)
        self.assertEqual(sum("Playwright" in d for d in self.run_clear()), 1)
        for never in ("claude", "Google Chrome", "limactl"):
            self.assertNotIn(never, done)

    def test_lock_holder_tree_is_spared(self):
        self.assertFalse(any("4391" in d for d in self.run_clear(holder=900)))
        self.assertTrue(any("4391" in d for d in self.run_clear()))

    def test_trigger(self):
        self.assertTrue(mg.triggered(7, 9, 1)[0])      # 78% swap
        self.assertTrue(mg.triggered(1, 9, 2)[0])      # pressure warning
        self.assertFalse(mg.triggered(3, 9, 1)[0])


if __name__ == "__main__":
    unittest.main()
