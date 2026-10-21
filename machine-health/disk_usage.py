"""disk-usage (a machine-health check): REPORT ONLY. Once a day logs free space and the biggest folders under ~;
warns (one ntfy, at most once a day) when the data volume has less than 15% free. Deletes nothing.
Config: MACHINE_HEALTH_DISK_WARN_PCT (default 15)."""
import os, re, shutil, subprocess, time

import stuck_watch as sw

HOME = os.path.expanduser("~")
VOLUME = "/System/Volumes/Data" if os.path.isdir("/System/Volumes/Data") else "/"


def free_pct(path=VOLUME):
    u = shutil.disk_usage(path)
    return u.free * 100 / u.total, u.free / 2**30, u.total / 2**30


def top_folders(n=6):
    """Biggest folders directly under ~ (sizes only; the secrets folder is skipped)."""
    try:
        out = subprocess.run(["du", "-xk", "-d", "1", "-I", "secrets", HOME], capture_output=True, text=True, timeout=600).stdout
    except subprocess.TimeoutExpired:
        return []
    rows = [(int(k), p) for k, p in (l.split("\t", 1) for l in out.splitlines() if "\t" in l) if p.rstrip("/") != HOME]
    return [(k / 1048576, p.replace(HOME, "~")) for k, p in sorted(rows, reverse=True)[:n]]


def decide_warn(pct, limit):
    return pct < limit


def run(dry=False, force=False):
    pct, free, total = free_pct()
    limit = float(sw.conf("MACHINE_HEALTH_DISK_WARN_PCT", 15))
    top = top_folders()
    summary = f"{free:.0f} of {total:.0f} GB free ({pct:.0f}%)"
    sw.log("DISK", summary + "; biggest under ~: " + ", ".join(f"{p} {g:.1f} GB" for g, p in top))
    if decide_warn(pct, limit) and not dry:
        sw.notify(f"Low disk on {sw.HOST}: {summary}. Biggest: " + ", ".join(f"{p} {g:.0f} GB" for g, p in top[:4]), 4)
    return summary + (" LOW" if decide_warn(pct, limit) else "")
