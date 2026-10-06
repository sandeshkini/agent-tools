"""Helpers for components that are one LaunchAgent (macOS) or one systemd --user unit (Linux)."""
from ..core import CHANGE, GAP, OK, Plan


def describe(job, home=""):
    """'com.x runs as "Name" (pa-app)' or 'com.x runs python3.12 ~/path/script.py'."""
    n = job.get("named")
    if n:
        return f'{job["label"]} runs as "{n["name"]}" ({n["kind"]})'
    runs = job.get("runs", "")
    if home:
        runs = runs.replace(home, "~")
    return f'{job["label"]} runs {runs}'


def plan_launch_agent(jobs, install_action, home="", blockers=()):
    """Plan for a component that is a LaunchAgent: in place if one is loaded, reload if installed but not
    loaded, else install (unless a blocker, e.g. a missing prerequisite, makes it a GAP)."""
    loaded = [j for j in jobs if j["loaded"]]
    if loaded:
        notes = []
        if len(loaded) > 1:
            notes.append("more than one job does this: " + ", ".join(j["label"] for j in loaded) +
                         " (remove the extra one by hand)")
        j = loaded[0]
        state = "running" if j.get("state") == "running" else ("loaded" if j.get("keepalive") else "scheduled")
        return Plan(OK, f"{describe(loaded[0], home)} ({state})", notes=notes)
    if jobs:
        j = jobs[0]
        return Plan(CHANGE, f"{j['label']} is installed but not loaded",
                    actions=[f"load it again (launchctl bootstrap {j['plist'].replace(home, '~') if home else j['plist']})"],
                    data={"do": "load", "label": j["label"], "plist": j["plist"]})
    if blockers:
        return Plan(GAP, "not installed; " + "; ".join(blockers), human=list(blockers))
    return Plan(CHANGE, "not installed", actions=[install_action], data={"do": "install"})


def plan_systemd(state, unit, install_action=None, blocker=None):
    """Plan for a systemd --user unit (usually a timer)."""
    if state.get("enabled") == "enabled":
        return Plan(OK, f"{unit} enabled ({state.get('active')})")
    if install_action:
        return Plan(CHANGE, f"{unit} is {state.get('enabled') or 'not installed'}", actions=[install_action],
                    data={"do": "install"})
    return Plan(GAP, f"{unit} is {state.get('enabled') or 'not installed'}; {blocker}", human=[blocker] if blocker else [])
