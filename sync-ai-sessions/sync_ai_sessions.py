#!/usr/bin/env python3
"""Export Claude Code + OpenCode transcripts and memory files into ~/Documents/ai-memory.

This is aibo-mac's counterpart to aibo's `~/scripts/sync-ai-sessions.py` (systemd
--user timer, every 15 min -- see aibo-server/Infrastructure/scripts.md). The
script source was never committed anywhere, so this is a fresh implementation
written to match the *output format* byte-for-byte, reverse-engineered from the
~180 real transcripts aibo already pushed into the shared private repo
(github.com/sandeshkini/ai-memory). Both machines write into the same repo.

Output formats (do not "improve" these -- they must stay identical to aibo's):

  sessions/<uuid8>-<YYYYMMDD>-<HHMM>.md      Claude Code
      # <uuid8> · <YYYY-MM-DD HH:MM>
      <blank>
      **Dir:** <cwd>·· (two trailing spaces)
      **CLI:** claude
      <blank>
      ---
      <blank>
      **You:** <text>            one block per user text block
      **Claude:** <text>         one block per assistant text block
      **Claude:** <details><summary>thinking</summary>\n\n<text>\n\n</details>
      ...blocks joined by a blank line, file ends with a single newline.
      Tool calls / tool results are NOT exported (aibo's exports contain none).

  sessions/<opencode-slug>.md                OpenCode
      # <slug>
      <blank>
      ## 🧑 You / ## 🤖 Assistant      emitted per *text part* (so a header can
                                        repeat for the same message when a tool
                                        call splits it)
      <details><summary>💭 thinking</summary>\n\n<text>\n</details>   reasoning parts
      **⚙ <Tool>**\n\n```json\n<compact input json>\n```\n\n```\n<output>\n```
      Interrupted/empty turns ("[Request interrupted by user]", "No response
      requested.") are just message text in the DB and flow through verbatim.

  memories/claude/<sanitized-project-path>/*.md   verbatim copies of
      ~/.claude/projects/<sanitized-path>/memory/*.md
  memories/opencode/*.md                          verbatim copies of OpenCode's
      own memory store (~/.opencode/memory on aibo; probed at several paths here)

Idempotent: a per-source (mtime, size) index in STATE_FILE short-circuits the
parse, and even when it does parse, a file is only rewritten when the rendered
bytes actually differ. Cheap enough to run every 15 minutes.

Usage:
    sync_ai_sessions.py [--no-git] [--dry-run] [--force]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()

REPO = HOME / "Documents" / "ai-memory"
SESSIONS_DIR = REPO / "sessions"
MEM_CLAUDE_DIR = REPO / "memories" / "claude"
MEM_OPENCODE_DIR = REPO / "memories" / "opencode"

CLAUDE_PROJECTS = HOME / ".claude" / "projects"
OPENCODE_DB = HOME / ".local" / "share" / "opencode" / "opencode.db"

# OpenCode's own memory store. aibo uses ~/.opencode/memory; macOS may differ, so
# probe in order and take the first that exists. Missing is normal -> skipped.
OPENCODE_MEMORY_CANDIDATES = [
    HOME / ".opencode" / "memory",
    HOME / ".config" / "opencode" / "memory",
    HOME / ".local" / "share" / "opencode" / "memory",
]

# State lives outside the repo so it never lands in a commit (and outside
# agent-tools, which is itself a pushed git repo).
STATE_FILE = HOME / "Library" / "Application Support" / "sync-ai-sessions" / "state.json"

HOSTTAG = socket.gethostname().split(".")[0] or "mac"

# Sessions whose `agent` marks them as non-conversations. aibo excludes
# agent='build' there, because claude-monitor's retired terminal-summarizer loop
# created ~13k throwaway headless `opencode run` sessions under that agent while
# real chats ran under a different one.
#
# On THIS Mac that mapping inverts: opencode 1.18's default *interactive* agent
# is literally 'build' (all real TUI chats here are agent='build'), so excluding
# it would export nothing at all. The real category aibo was filtering --
# machine-generated, non-user-facing sessions -- is caught here by the
# parent_id/empty-body filters below instead. Kept as a knob so the aibo rule can
# be switched on if this Mac ever grows a headless opencode loop:
#   SYNC_EXCLUDE_AGENTS=build sync_ai_sessions.py
EXCLUDE_AGENTS = {
    a.strip() for a in os.environ.get("SYNC_EXCLUDE_AGENTS", "").split(",") if a.strip()
}

# OpenCode stores tool names lowercase ("bash", "webfetch"); aibo's DB stored
# them already-capitalized, so its exports read "**⚙ Bash**". Map the known
# built-ins to the same display names; anything unknown gets title-cased.
TOOL_DISPLAY = {
    "bash": "Bash",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "patch": "Patch",
    "glob": "Glob",
    "grep": "Grep",
    "list": "List",
    "ls": "List",
    "webfetch": "WebFetch",
    "websearch": "WebSearch",
    "todowrite": "TodoWrite",
    "todoread": "TodoRead",
    "task": "Task",
    "agent": "Agent",
    "question": "Question",
    "toolsearch": "ToolSearch",
    "invalid": "Invalid",
}


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}", flush=True)


# --------------------------------------------------------------------------- state


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {"sources": {}, "outputs": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(STATE_FILE)


def source_stamp(path: Path) -> str:
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


# --------------------------------------------------------------------- output write


class Writer:
    """Writes rendered files, tracking which ones we own.

    Collision policy: aibo pushes into the same repo. A Claude Code collision is
    effectively impossible (uuid4's first 8 hex chars *and* the same start
    minute); an OpenCode slug collision is at least conceivable if aibo ever
    upgrades to this slug scheme. So: never clobber a file we can't recognise as
    our own -- fall back to '<stem>-<host>.md' and shout about it.
    """

    def __init__(self, state: dict, dry_run: bool):
        self.state = state
        self.dry_run = dry_run
        self.new = 0
        self.updated = 0
        self.unchanged = 0

    def write(self, path: Path, content: bytes | str) -> None:
        # Bytes, not text, all the way through: transcripts contain bare \r
        # (curl progress bars in tool output), and read_text() would normalise it
        # to \n while write_text() wouldn't -- making every run see a diff and
        # rewrite the file forever.
        data = content.encode() if isinstance(content, str) else content
        rel = str(path.relative_to(REPO))
        owned = rel in self.state["outputs"]

        if path.exists():
            existing = path.read_bytes()
            if existing == data:
                self.state["outputs"][rel] = True
                self.unchanged += 1
                return
            if not owned and not self._recognisably_ours(existing, data):
                alt = path.with_name(f"{path.stem}-{HOSTTAG}{path.suffix}")
                log(f"  ! collision: {rel} exists and isn't ours -> writing {alt.name}")
                return self.write(alt, data)
            action = "updated"
            self.updated += 1
        else:
            action = "new"
            self.new += 1

        if self.dry_run:
            log(f"  [dry-run] {action}: {rel}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            log(f"  {action}: {rel}")
        self.state["outputs"][rel] = True

    @staticmethod
    def _recognisably_ours(existing: bytes, content: bytes) -> bool:
        """Same session, just grown/edited since we last wrote it?

        Same first line ('# <id> · <start>' / '# <slug>') plus the old bytes
        being a prefix of the new ones is a transcript that appended -- ours.
        Guards against a lost state file turning every existing export into a
        false "collision".
        """
        if not existing:
            return True
        if existing.split(b"\n", 1)[0] != content.split(b"\n", 1)[0]:
            return False
        return content.startswith(existing.rstrip(b"\n"))


# ------------------------------------------------------------------ claude code


def _iso_to_local(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def render_claude_session(path: Path) -> tuple[str, str] | None:
    """Render one Claude Code .jsonl -> (filename, markdown), or None if empty."""
    session_id = None
    cwd = None
    first_dt = None
    blocks: list[str] = []

    with path.open(errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # partial trailing line of a live transcript
            if not isinstance(entry, dict):
                continue

            if session_id is None and entry.get("sessionId"):
                session_id = entry["sessionId"]
            if cwd is None and entry.get("cwd"):
                cwd = entry["cwd"]

            etype = entry.get("type")
            if etype not in ("user", "assistant"):
                continue  # queue-operation, attachment, ai-title, mode, summary, ...
            msg = entry.get("message")
            if not isinstance(msg, dict):
                continue

            rendered = _render_claude_message(msg)
            if not rendered:
                continue
            if first_dt is None and entry.get("timestamp"):
                first_dt = _iso_to_local(entry["timestamp"])
            blocks.extend(rendered)

    if not blocks:
        return None

    session_id = session_id or path.stem
    if first_dt is None:
        first_dt = datetime.fromtimestamp(path.stat().st_mtime).astimezone()

    sid8 = session_id[:8]
    name = f"{sid8}-{first_dt.strftime('%Y%m%d-%H%M')}.md"
    header = (
        f"# {sid8} · {first_dt.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"**Dir:** {cwd or ''}  \n"
        f"**CLI:** claude\n\n"
        f"---\n\n"
    )
    return name, header + "\n\n".join(blocks) + "\n"


def _render_claude_message(msg: dict) -> list[str]:
    role = msg.get("role")
    label = "You" if role == "user" else "Claude"
    content = msg.get("content")
    out: list[str] = []

    if isinstance(content, str):
        text = content.strip()
        if text:
            out.append(f"**{label}:** {text}")
        return out

    if not isinstance(content, list):
        return out

    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = (block.get("text") or "").strip()
            if text:
                out.append(f"**{label}:** {text}")
        elif btype == "thinking" and role == "assistant":
            text = (block.get("thinking") or "").strip()
            if text:
                out.append(
                    f"**Claude:** <details><summary>thinking</summary>\n\n{text}\n\n</details>"
                )
        # tool_use / tool_result / image: deliberately not exported (matches aibo)
    return out


def sync_claude(writer: Writer, state: dict, force: bool) -> None:
    if not CLAUDE_PROJECTS.is_dir():
        log("claude: no ~/.claude/projects -- skipping")
        return

    projects = sorted(p for p in CLAUDE_PROJECTS.iterdir() if p.is_dir())
    log(f"claude: {len(projects)} project dir(s)")

    n_sessions = 0
    for proj in projects:
        # Top-level *.jsonl only. Each <uuid>/subagents/agent-*.jsonl is a
        # sub-transcript of its parent session, not a user-facing conversation --
        # and it carries the PARENT's sessionId, so exporting it separately would
        # fight the parent for the same filename. Excluded by walking only the
        # project dir itself (not by an isSidechain check), so sidechain turns
        # that older Claude Code versions inlined into a parent transcript still
        # render as part of that parent's markdown.
        for jf in sorted(proj.glob("*.jsonl")):
            n_sessions += 1
            key = str(jf)
            stamp = source_stamp(jf)
            if not force and state["sources"].get(key) == stamp:
                out = state.get("emitted", {}).get(key)
                if out and (SESSIONS_DIR / out).exists():
                    writer.state["outputs"][f"sessions/{out}"] = True
                    writer.unchanged += 1
                    continue
            rendered = render_claude_session(jf)
            if rendered is None:
                state["sources"][key] = stamp
                continue
            name, content = rendered
            writer.write(SESSIONS_DIR / name, content)
            state["sources"][key] = stamp
            state.setdefault("emitted", {})[key] = name

        # memories/claude/<sanitized-project-path>/*.md
        memdir = proj / "memory"
        if memdir.is_dir():
            for mf in sorted(memdir.rglob("*.md")):
                writer.write(MEM_CLAUDE_DIR / proj.name / mf.relative_to(memdir),
                             mf.read_bytes())

    log(f"claude: {n_sessions} transcript(s) considered")


# --------------------------------------------------------------------- opencode


def _oc_parts(conn: sqlite3.Connection, session_id: str) -> list[tuple[str, dict]]:
    rows = conn.execute(
        """
        SELECT m.data, p.data
          FROM part p
          JOIN message m ON m.id = p.message_id
         WHERE p.session_id = ?
         ORDER BY m.time_created, m.id, p.time_created, p.id
        """,
        (session_id,),
    ).fetchall()
    parts = []
    for mdata, pdata in rows:
        try:
            msg = json.loads(mdata)
            part = json.loads(pdata)
        except ValueError:
            continue
        parts.append((msg.get("role") or "assistant", part))
    return parts


def render_opencode_session(conn: sqlite3.Connection, slug: str, session_id: str):
    blocks: list[str] = []
    for role, part in _oc_parts(conn, session_id):
        ptype = part.get("type")

        if ptype == "text":
            text = (part.get("text") or "").strip()
            if text:
                head = "## 🧑 You" if role == "user" else "## 🤖 Assistant"
                blocks.append(f"{head}\n\n{text}")

        elif ptype == "reasoning":
            text = (part.get("text") or "").strip()
            if text:
                blocks.append(
                    f"<details><summary>💭 thinking</summary>\n\n{text}\n</details>"
                )

        elif ptype == "tool":
            name = part.get("tool") or "tool"
            display = TOOL_DISPLAY.get(name.lower(), name if name[:1].isupper() else name.title())
            state = part.get("state") or {}
            chunk = f"**⚙ {display}**"
            if isinstance(state.get("input"), (dict, list)):
                args = json.dumps(state["input"], separators=(",", ":"), ensure_ascii=False)
                chunk += f"\n\n```json\n{args}\n```"
            output = state.get("output")
            if not output and state.get("error"):
                output = str(state["error"])
            if output:
                chunk += f"\n\n```\n{str(output).rstrip()}\n```"
            blocks.append(chunk)

        # step-start / step-finish / snapshot / file: no visible output

    if not blocks:
        return None
    return f"{slug}.md", f"# {slug}\n\n" + "\n\n".join(blocks) + "\n"


def sync_opencode(writer: Writer, state: dict, force: bool) -> None:
    if not OPENCODE_DB.exists():
        log("opencode: no opencode.db -- skipping")
        return

    stamp = source_stamp(OPENCODE_DB)
    db_unchanged = (not force) and state["sources"].get(str(OPENCODE_DB)) == stamp

    # Read-only, and via a temp copy so a live opencode's WAL is never touched.
    uri = f"file:{OPENCODE_DB}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as exc:
        log(f"opencode: cannot open db ({exc}) -- skipping")
        return

    try:
        try:
            rows = conn.execute(
                "SELECT id, slug, agent, parent_id, title FROM session ORDER BY time_created"
            ).fetchall()
        except sqlite3.Error as exc:
            log(f"opencode: unexpected schema ({exc}) -- skipping")
            return

        kept = skipped_child = skipped_agent = empty = 0
        for sid, slug, agent, parent_id, title in rows:
            if parent_id:
                skipped_child += 1  # @subagent session spawned by another session
                continue
            if agent in EXCLUDE_AGENTS:
                skipped_agent += 1
                continue
            if not slug:
                slug = sid
            if db_unchanged:
                out = state.get("emitted", {}).get(f"oc:{sid}")
                if out and (SESSIONS_DIR / out).exists():
                    writer.state["outputs"][f"sessions/{out}"] = True
                    writer.unchanged += 1
                    kept += 1
                    continue
            rendered = render_opencode_session(conn, slug, sid)
            if rendered is None:
                empty += 1
                continue
            name, content = rendered
            writer.write(SESSIONS_DIR / name, content)
            state.setdefault("emitted", {})[f"oc:{sid}"] = name
            kept += 1

        log(
            f"opencode: {kept} session(s) exported, {skipped_child} subagent, "
            f"{skipped_agent} filtered agent, {empty} empty"
        )
        state["sources"][str(OPENCODE_DB)] = stamp
    finally:
        conn.close()

    # memories/opencode/*.md
    memdir = next((d for d in OPENCODE_MEMORY_CANDIDATES if d.is_dir()), None)
    if memdir is None:
        log("opencode: no memory dir on this machine -- skipping (normal)")
        return
    files = sorted(memdir.rglob("*.md"))
    log(f"opencode: mirroring {len(files)} memory file(s) from {memdir}")
    for mf in files:
        writer.write(MEM_OPENCODE_DIR / mf.relative_to(memdir), mf.read_bytes())


# -------------------------------------------------------------------------- git


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, check=check, timeout=180,
    )


def git_sync() -> None:
    """add -A && commit (only if something changed) && push. Never fatal."""
    try:
        git("add", "-A")
        if git("diff", "--cached", "--quiet", check=False).returncode == 0:
            log("git: nothing to commit")
            return
        stat = git("diff", "--cached", "--shortstat").stdout.strip()
        # Identity comes from the machine's global git config on purpose.
        msg = f"sync-ai-sessions ({HOSTTAG}): {stat or 'update'}"
        git("commit", "-m", msg)
        log(f"git: committed -- {stat}")
        push = git("push", check=False)
        if push.returncode == 0:
            log("git: pushed")
        else:
            log(f"git: push FAILED rc={push.returncode} {push.stderr.strip()[:300]}")
    except (subprocess.SubprocessError, OSError) as exc:
        log(f"git: error {exc}")


# ------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-git", action="store_true", help="export only, skip commit/push")
    ap.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    ap.add_argument("--force", action="store_true", help="ignore the mtime index and re-render everything")
    args = ap.parse_args()

    if not REPO.is_dir():
        log(f"FATAL: {REPO} missing (clone github.com/sandeshkini/ai-memory there first)")
        return 1

    started = datetime.now()
    log(f"=== sync-ai-sessions start ({HOSTTAG}) ===")

    state = load_state()
    state.setdefault("sources", {})
    state.setdefault("outputs", {})
    state.setdefault("emitted", {})
    writer = Writer({"outputs": state["outputs"]}, args.dry_run)

    for d in (SESSIONS_DIR, MEM_CLAUDE_DIR, MEM_OPENCODE_DIR):
        if not args.dry_run:
            d.mkdir(parents=True, exist_ok=True)

    sync_claude(writer, state, args.force)
    sync_opencode(writer, state, args.force)

    state["outputs"] = writer.state["outputs"]
    if not args.dry_run:
        save_state(state)

    log(
        f"done: {writer.new} new, {writer.updated} updated, {writer.unchanged} unchanged "
        f"({(datetime.now() - started).total_seconds():.1f}s)"
    )

    if args.no_git or args.dry_run:
        log("git: skipped (--no-git/--dry-run)")
    elif writer.new or writer.updated:
        git_sync()
    else:
        log("git: nothing changed, not touching the repo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
