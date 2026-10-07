#!/usr/bin/env python3
"""
macOS wrapper around sync_ai_sessions.py (same folder; that file is the one shared script; aibo-linux
runs the same file -- edit sync logic there, not here).

On aibo-linux, the git commit+push for ai-memory is handled by a separate
nightly backup.sh. aibo-mac has no equivalent always-on backup job, so this
wrapper does it inline after every run instead.

Must run the sync step **in-process** (import + call, not subprocess.run of a
second python) -- under launchd, macOS TCC's directory-open consent check for
~/Documents/~/.claude hangs forever (no GUI to show the prompt to) when a
*nested* subprocess (python spawning a second python that then opens the
protected dir) does the open() call, even though the exact same open() from
a single directly-launchd-invoked python process returns instantly. Confirmed
by testing (`sample` showed the child stuck in os_scandir -> open$NOCANCEL).
git is fine as a subprocess.run() from here -- that's how the previous
(pre-consolidation) version did it successfully under the same launchd setup.
"""
import runpy
import subprocess
from datetime import datetime
from pathlib import Path

HOME = Path.home()
REPO = next((p for p in (HOME / "Documents" / "personal" / "ai-memory", HOME / "Documents" / "ai-memory")
             if (p / ".git").is_dir()), HOME / "Documents" / "personal" / "ai-memory")
SYNC_SCRIPT = Path(__file__).resolve().parent / "sync_ai_sessions.py"


def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, **kw)


def main():
    print(f"{ts()}  === sync-ai-sessions start (aibo-mac) ===", flush=True)
    try:
        runpy.run_path(str(SYNC_SCRIPT), run_name="__main__")
    except SystemExit:
        pass
    except Exception as e:
        print(f"{ts()}  sync FAILED: {e!r}, skipping git", flush=True)
        return

    status = run(["git", "status", "--porcelain"])
    if status.returncode != 0:
        print(f"{ts()}  git status failed: {status.stderr.strip()}", flush=True)
        return
    if not status.stdout.strip():
        print(f"{ts()}  git: nothing changed", flush=True)
        return

    run(["git", "add", "-A"])
    commit = run(["git", "commit", "-q", "-m", f"sync: {datetime.now().strftime('%Y-%m-%d %H:%M')} from aibo-mac"])
    if commit.returncode != 0:
        print(f"{ts()}  git commit failed: {commit.stderr.strip()}", flush=True)
        return
    push = run(["git", "push", "-q", "origin", "main"])
    if push.returncode != 0:
        # Another machine pushed first (aibo-linux and the laptops write here too): rebase onto it
        # and try once more instead of waiting for Repo Sync's next pass.
        pull = run(["git", "pull", "--rebase", "--autostash", "-q", "origin", "main"])
        if pull.returncode == 0:
            push = run(["git", "push", "-q", "origin", "main"])
        else:
            run(["git", "rebase", "--abort"])
    if push.returncode == 0:
        print(f"{ts()}  git: committed + pushed", flush=True)
    else:
        print(f"{ts()}  git: committed locally, PUSH FAILED ({push.stderr.strip()}), will retry next run", flush=True)


if __name__ == "__main__":
    main()
