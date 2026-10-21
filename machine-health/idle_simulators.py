"""idle-simulators (a machine-health check): shut down iOS Simulators that have been booted for hours with nobody
using them (3 of them ran for 3 days and ate over 20 GB, 2026-10-21).

A simulator's boot time is the age of its `launchd_sim` process (the path holds the device UDID). If any has been
up longer than the threshold (default 2 h) and no build or test is active (xcodebuild, XCBBuildService, simctl,
xcrun simctl, xctest, xcodebuild test runners), run `xcrun simctl shutdown all`, log it and alert once.
Config: MACHINE_HEALTH_SIM_IDLE_S (seconds, default 7200).
"""
import re, subprocess, time

import stuck_watch as sw
from stale_prompts import etime_s

ACTIVE = re.compile(r"(^|/)(xcodebuild|XCBBuildService|xctest|swift-build|testmanagerd|IDETestRunner|xcrun)( |$)|"
                    r"(^|/)simctl( |$)|xcodebuild|xctest", re.I)
UDID = re.compile(r"Devices/([0-9A-F]{8}-(?:[0-9A-F]{4}-){3}[0-9A-F]{12})", re.I)


def threshold():
    return int(sw.conf("MACHINE_HEALTH_SIM_IDLE_S", 7200))


def parse(ps_out, own_pid=None):
    """-> ({udid: age_s} of booted simulators, [active build/test command lines])."""
    sims, active = {}, []
    for line in ps_out.splitlines():
        m = re.match(r"\s*(\d+)\s+(\S+)\s+(.*)", line)
        if not m:
            continue
        pid, et, args = int(m.group(1)), m.group(2), m.group(3)
        if "launchd_sim" in args:
            u = UDID.search(args)
            if u:
                sims[u.group(1).upper()] = max(sims.get(u.group(1).upper(), 0), etime_s(et))
        elif pid != own_pid and "machine_health" not in args and ACTIVE.search(args.split(" -")[0][:200]):
            active.append(args[:100])
    return sims, active


def decide(sims, active, limit):
    """-> (action, text) where action is 'shutdown' | 'none'."""
    old = {u: a for u, a in sims.items() if a > limit}
    if not sims:
        return "none", "no simulators booted"
    if not old:
        return "none", f"{len(sims)} booted, none over {limit // 60} min"
    if active:
        return "none", f"{len(old)} booted over {limit // 60} min but a build/test is active ({active[0]})"
    return "shutdown", f"{len(old)} of {len(sims)} simulators booted up to {max(old.values()) / 3600:.1f} h with no build/test"


def run(dry=False, force=False):
    import os
    out = subprocess.run(["ps", "-axo", "pid=,etime=,args="], capture_output=True, text=True).stdout
    sims, active = parse(out, os.getpid())
    action, text = decide(sims, active, threshold())
    if action == "shutdown":
        sw.log("SIM", f"{'[dry] ' if dry else ''}shutting down all simulators: {text}")
        if not dry:
            subprocess.run(["xcrun", "simctl", "shutdown", "all"], capture_output=True, timeout=120)
            sw.notify(f"Shut down idle iOS Simulators on {sw.HOST}: {text}. They hold gigabytes of RAM.", 3)
    return text
