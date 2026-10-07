#!/usr/bin/env python3
"""
Continuously syncs AI coding-agent sessions and memories into
Documents/personal/ai-memory/ (its own git repo, sandeshkini/ai-memory -- moved out
of loose Documents/ subfolders 2026-08-15 so this data actually has backup
coverage), system-wide (not tied to any one project or hook):

  - Claude Code: every project under ~/.claude/projects/*/ -> readable
    transcripts in ai-memory/sessions/, and every project's memory/ dir
    -> ai-memory/memories/claude/<project-slug>/
  - OpenCode: every session in ~/.local/share/opencode/opencode.db ->
    readable transcripts in ai-memory/sessions/, and ~/.opencode/memory/
    -> ai-memory/memories/opencode/

Both are fully dynamic -- CLAUDE_PROJECTS.iterdir() walks whatever project
dirs exist at run time, not a fixed list, so a brand-new project directory
gets picked up on the next 15-minute timer run with no code change needed.

Idempotent: re-running only rewrites a transcript if its source changed
since the last export (mtime-gated), so this is cheap to run on a timer.

This is the one copy, used by every machine (agent-tools is cloned at the same path everywhere):
aibo-linux runs it from a systemd --user timer, aibo-mac through mac_wrapper.py. See README.md.
"""
import json
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

HOME = Path.home()


def _resolve_ai_memory_root():
    """Locate the ai-memory checkout. Portable across machines:

      1. $AI_MEMORY_DIR wins (explicit override, e.g. a non-standard checkout)
      2. otherwise the first known layout that actually exists on this host
      3. otherwise the default, which gets created

    This exists because the repo copy and the deployed copy had silently
    diverged on this path, which would have redirected the sync into an
    empty tree on one of the two machines.
    """
    env = os.environ.get("AI_MEMORY_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    # The current layout first (both machines since 2026-10-07). The old one is only a fallback: if
    # something ever recreates ~/Documents/ai-memory, the exports must not follow it.
    candidates = [
        HOME / "Documents" / "personal" / "ai-memory",
        HOME / "Documents" / "ai-memory",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


AI_MEMORY_ROOT = _resolve_ai_memory_root()
SESSIONS_DIR = AI_MEMORY_ROOT / "sessions"
MEMORIES_DIR = AI_MEMORY_ROOT / "memories"
SUBAGENTS_DIR = SESSIONS_DIR / "subagents"
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
OPENCODE_DB = HOME / ".local" / "share" / "opencode" / "opencode.db"
OPENCODE_MEMORY = HOME / ".opencode" / "memory"

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
SUBAGENTS_DIR.mkdir(parents=True, exist_ok=True)


def stamp(dt):
    return dt.strftime("%Y%m%d-%H%M")


# ─────────────────────────── Claude Code ───────────────────────────────────

def extract_text_blocks(content, want_types):
    """content is either a str or a list of content blocks."""
    if isinstance(content, str):
        return [content] if "text" in want_types else []
    out = []
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text" and "text" in want_types:
                out.append(b.get("text", ""))
            elif t == "thinking" and "thinking" in want_types:
                th = b.get("thinking", "")
                if th.strip():
                    out.append(f"<details><summary>thinking</summary>\n\n{th}\n\n</details>")
    return out


def render_claude_transcript(jsonl_path, project_label, parent_id=None):
    turns = []
    session_id = jsonl_path.stem
    first_ts = None
    with open(jsonl_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = d.get("timestamp")
            if ts and not first_ts:
                first_ts = ts
            dtype = d.get("type")
            if dtype not in ("user", "assistant"):
                continue
            msg = d.get("message", {})
            role = msg.get("role")
            content = msg.get("content")
            if role == "user":
                texts = extract_text_blocks(content, {"text"})
                # skip pure tool-result / system-reminder noise: only keep if
                # it looks like a plain string turn, or a short-ish text block
                if isinstance(content, str) and content.strip():
                    turns.append(("You", content.strip()))
                elif texts:
                    joined = "\n".join(t for t in texts if t.strip())
                    if joined.strip():
                        turns.append(("You", joined.strip()))
            elif role == "assistant":
                texts = extract_text_blocks(content, {"text", "thinking"})
                joined = "\n\n".join(t for t in texts if t.strip())
                if joined.strip():
                    turns.append(("Claude", joined.strip()))

    if not turns:
        return None

    try:
        created = datetime.fromisoformat(first_ts.replace("Z", "+00:00")) if first_ts else datetime.fromtimestamp(jsonl_path.stat().st_mtime)
    except Exception:
        created = datetime.fromtimestamp(jsonl_path.stat().st_mtime)

    when = created.strftime('%Y-%m-%d %H:%M')
    if parent_id:
        lines = [
            f"# subagent {session_id} · {when}",
            "",
            f"**Dir:** {project_label}  ",
            f"**Parent session:** {parent_id}  ",
            "**CLI:** claude (subagent)",
            "",
            "---",
            "",
        ]
    else:
        lines = [
            f"# {session_id[:8]} · {when}",
            "",
            f"**Dir:** {project_label}  ",
            "**CLI:** claude",
            "",
            "---",
            "",
        ]
    for who, text in turns:
        lines.append(f"**{who}:** {text}")
        lines.append("")
    return "\n".join(lines), created


def sync_claude_sessions():
    if not CLAUDE_PROJECTS.exists():
        return 0, 0, 0
    written = 0
    memory_synced = 0
    sub_written = 0
    for project_dir in CLAUDE_PROJECTS.iterdir():
        if not project_dir.is_dir():
            continue
        # decode project path back from the sanitized dir name (best-effort)
        project_label = project_dir.name.replace("-", "/")
        for jsonl in project_dir.glob("*.jsonl"):
            out_prefix = jsonl.stem[:8]
            # find any existing export for this session id to gate on mtime
            existing = list(SESSIONS_DIR.glob(f"{out_prefix}-*.md"))
            if existing and existing[0].stat().st_mtime >= jsonl.stat().st_mtime:
                continue
            result = render_claude_transcript(jsonl, project_label)
            if not result:
                continue
            content, created = result
            out_path = SESSIONS_DIR / f"{out_prefix}-{stamp(created)}.md"
            for old in existing:
                if old != out_path:
                    old.unlink(missing_ok=True)
            out_path.write_text(content, encoding="utf-8")
            written += 1

        # ── subagent transcripts ──────────────────────────────────────────
        # Layout: <project>/<session-uuid>/subagents/agent-<id>.jsonl
        # The loop above is a NON-recursive glob, so every sub-agent
        # transcript used to be skipped and lost. rglob (not a fixed depth)
        # so a layout change doesn't silently start dropping them again.
        # Written to sessions/subagents/ -- a subdirectory, so the
        # non-recursive main-session glob above can never collide with (or
        # delete) these during its stale-export cleanup.
        for sub_dir in project_dir.rglob("subagents"):
            if not sub_dir.is_dir():
                continue
            parent_id = sub_dir.parent.name
            for jsonl in sub_dir.glob("*.jsonl"):
                agent_id = jsonl.stem.replace("agent-", "")
                out_prefix = f"{parent_id[:8]}-sub-{agent_id[:8]}"
                existing = list(SUBAGENTS_DIR.glob(f"{out_prefix}-*.md"))
                if existing and existing[0].stat().st_mtime >= jsonl.stat().st_mtime:
                    continue
                result = render_claude_transcript(jsonl, project_label, parent_id=parent_id)
                if not result:
                    continue
                content, created = result
                out_path = SUBAGENTS_DIR / f"{out_prefix}-{stamp(created)}.md"
                for old in existing:
                    if old != out_path:
                        old.unlink(missing_ok=True)
                out_path.write_text(content, encoding="utf-8")
                sub_written += 1

        mem_src = project_dir / "memory"
        if mem_src.is_dir() and any(mem_src.iterdir()):
            mem_dst = MEMORIES_DIR / "claude" / project_dir.name
            mem_dst.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["rsync", "-a", "--delete", f"{mem_src}/", f"{mem_dst}/"],
                check=False,
            )
            memory_synced += 1
    return written, memory_synced, sub_written


# ─────────────────────────── OpenCode ───────────────────────────────────────

def render_opencode_transcript(conn, session_row):
    sid, slug, title, directory, time_created = session_row
    msgs = conn.execute(
        "SELECT id, data FROM message WHERE session_id=? ORDER BY time_created", (sid,)
    ).fetchall()
    if not msgs:
        return None

    lines = [f"# oc-{slug}", "", f"**Dir:** {directory}  ", f"**Title:** {title}", "", "---", ""]
    any_content = False
    for msg_id, msg_data in msgs:
        try:
            md = json.loads(msg_data)
        except json.JSONDecodeError:
            continue
        role = md.get("role", "unknown")
        parts = conn.execute(
            "SELECT data FROM part WHERE message_id=? ORDER BY time_created", (msg_id,)
        ).fetchall()
        texts, thinking = [], []
        for (pdata,) in parts:
            try:
                pd = json.loads(pdata)
            except json.JSONDecodeError:
                continue
            ptype = pd.get("type")
            if ptype == "text" and pd.get("text", "").strip():
                texts.append(pd["text"].strip())
            elif ptype == "reasoning" and pd.get("text", "").strip():
                thinking.append(pd["text"].strip())
        if not texts and not thinking:
            continue
        any_content = True
        header = "## \U0001f9d1 You" if role == "user" else "## \U0001f916 Assistant"
        lines.append(header)
        lines.append("")
        if thinking:
            lines.append("<details><summary>\U0001f4ad thinking</summary>")
            lines.append("")
            lines.append("\n\n".join(thinking))
            lines.append("")
            lines.append("</details>")
            lines.append("")
        for t in texts:
            lines.append(t)
            lines.append("")

    if not any_content:
        return None
    created = datetime.fromtimestamp(time_created / 1000) if time_created else datetime.now()
    return "\n".join(lines), created


# claude-monitor (retired) ran a headless terminal summarizer through OpenCode and left ~13,000
# throwaway sessions behind, all starting with this prompt. They are the only OpenCode sessions
# skipped. (The old rule, agent != 'build', also dropped every real chat: 'build' is OpenCode's
# default agent, so every cptr and TUI chat has it.)
JUNK_PROMPT = "Summarize this terminal session in one short line"
# Optional extra exclusion by agent name, comma-separated (e.g. SYNC_EXCLUDE_AGENTS=build).
EXCLUDE_AGENTS = [a.strip() for a in os.environ.get("SYNC_EXCLUDE_AGENTS", "").split(",") if a.strip()]


def opencode_out_path(slug, sid):
    """oc-<slug>-<last 8 of the session id>.md. Slugs alone repeat (OpenCode reuses a small pool,
    and every machine writes into the same folder), so the old oc-<slug>.md names overwrote each
    other."""
    return SESSIONS_DIR / f"oc-{slug}-{sid[-8:]}.md"


def retire_legacy_export(slug, content):
    """Remove an old-style oc-<slug>.md once its session has been written under the new name, but
    only if that file is this session's transcript (same header, and its text is a start of the
    new one). Any other machine's or session's file with the same slug is left alone."""
    legacy = SESSIONS_DIR / f"oc-{slug}.md"
    if not legacy.is_file():
        return False
    try:
        old = legacy.read_bytes().decode("utf-8", errors="replace").rstrip()
    except OSError:
        return False
    if old and content.startswith(old):
        legacy.unlink(missing_ok=True)
        return True
    return False


def sync_opencode_sessions():
    if not OPENCODE_DB.exists():
        return 0
    written = 0
    conn = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True)
    try:
        sql = (
            "SELECT s.id, s.slug, s.title, s.directory, s.time_updated FROM session s "
            "WHERE NOT EXISTS (SELECT 1 FROM message m JOIN part p ON p.message_id = m.id "
            "  WHERE m.session_id = s.id AND json_extract(m.data, '$.role') = 'user' "
            "  AND p.data LIKE ?)"
        )
        args = [f"%{JUNK_PROMPT}%"]
        if EXCLUDE_AGENTS:
            sql += f" AND (s.agent IS NULL OR s.agent NOT IN ({','.join('?' * len(EXCLUDE_AGENTS))}))"
            args += EXCLUDE_AGENTS
        sessions = conn.execute(sql + " ORDER BY s.time_created", args).fetchall()
        for row in sessions:
            sid, slug, title, directory, time_updated = row
            out_path = opencode_out_path(slug, sid)
            src_mtime = (time_updated or 0) / 1000
            if out_path.exists() and out_path.stat().st_mtime >= src_mtime:
                continue
            result = render_opencode_transcript(conn, row)
            if not result:
                continue
            content, _created = result
            out_path.write_text(content, encoding="utf-8")
            os.utime(out_path, (src_mtime, src_mtime))
            retire_legacy_export(slug, content)
            written += 1
    finally:
        conn.close()
    return written


def sync_opencode_memory():
    if not OPENCODE_MEMORY.is_dir() or not any(OPENCODE_MEMORY.iterdir()):
        return False
    dst = MEMORIES_DIR / "opencode"
    dst.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-a", "--delete", f"{OPENCODE_MEMORY}/", f"{dst}/"], check=False)
    return True


def main():
    c_written, c_mem, c_sub = sync_claude_sessions()
    oc_written = sync_opencode_sessions()
    oc_mem = sync_opencode_memory()
    print(
        f"claude: {c_written} session(s) + {c_sub} subagent transcript(s) written/updated, "
        f"{c_mem} project memory dir(s) synced | "
        f"opencode: {oc_written} session(s) written/updated, memory synced={oc_mem}"
    )


if __name__ == "__main__":
    main()
