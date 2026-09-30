# sync-ai-sessions (aibo-mac)

Exports every Claude Code + OpenCode conversation on this Mac, plus both tools'
memory files, into `~/Documents/ai-memory/` (private repo
`github.com/sandeshkini/ai-memory`) and pushes. Runs every 15 minutes under
launchd.

This is the macOS counterpart of aibo's `~/scripts/sync-ai-sessions.py` +
`sync-ai-sessions.timer` (see `aibo-server/Infrastructure/scripts.md`). **Both
machines write into the same repo**, so the output format here was
reverse-engineered byte-for-byte from aibo's ~180 existing transcripts — aibo's
script source was never committed anywhere, so this is a fresh implementation of
the same contract, not a port.

Unlike aibo there's no nightly `backup.sh` on this Mac, so the git
`add`/`commit`/`push` is baked into the script itself (identity comes from the
machine's global git config).

## Install

```bash
./install.sh                       # venv + LaunchAgent, runs once at load
launchctl print gui/$(id -u)/com.sandesh.sync-ai-sessions   # verify
tail -f /tmp/sync-ai-sessions.log
```

## Run by hand

```bash
.venv/bin/python sync_ai_sessions.py            # normal: export + commit + push
.venv/bin/python sync_ai_sessions.py --no-git   # export only
.venv/bin/python sync_ai_sessions.py --dry-run  # report, write nothing
.venv/bin/python sync_ai_sessions.py --force    # ignore the mtime index, re-render all
```

## What it reads (read-only, always)

| Source | Becomes |
|---|---|
| `~/.claude/projects/*/[uuid].jsonl` | `sessions/<uuid8>-<YYYYMMDD>-<HHMM>.md` |
| `~/.claude/projects/*/memory/*.md` | `memories/claude/<sanitized-project-path>/*.md` |
| `~/.local/share/opencode/opencode.db` | `sessions/<opencode-slug>.md` |
| OpenCode's memory dir (probed, see below) | `memories/opencode/*.md` |

Nothing is hardcoded: project dirs are discovered by walking
`~/.claude/projects/`, OpenCode sessions by querying the DB. New ones appear on
the next run. The DB is opened `mode=ro`; nothing under `~/.claude` or
`~/.local/share/opencode` is ever written.

## Decisions worth knowing

**Subagent transcripts are not separate exports.** Claude Code 2.1.x writes
background-agent sub-transcripts to `<session-uuid>/subagents/agent-*.jsonl`.
Those are excluded — they aren't user-facing conversations, and every line in
them carries the *parent's* `sessionId`, so exporting them would fight the parent
for the same output filename. The exclusion is by location (only top-level
`*.jsonl` in each project dir is walked), **not** by an `isSidechain` test — so
sidechain turns that older Claude Code versions inlined into a parent transcript
still render as part of that parent's markdown. Nothing is lost that aibo's
format would have kept: the format carries no tool calls or tool results at all,
and a subagent's result comes back to the parent as a tool result.

**OpenCode's `agent='build'` filter is not applied here.** aibo excludes
`agent='build'` because claude-monitor's retired terminal-summarizer loop left
~13k throwaway headless sessions under that agent. On this Mac the mapping
inverts: opencode 1.18's default *interactive* agent is literally `build` — every
real TUI chat here is `agent='build'`, and the only non-`build` session is a
`@general` subagent. Applying aibo's literal rule would export nothing and keep
only the subagent. The same *category* (machine-generated non-conversations) is
filtered instead by:

- `parent_id IS NOT NULL` → a session spawned by another session (subagent),
  the OpenCode analogue of the Claude Code decision above;
- sessions that render to nothing (no text/reasoning/tool parts).

The aibo rule is still one env var away if this Mac ever grows a headless loop:
`SYNC_EXCLUDE_AGENTS=build`.

**OpenCode memory store.** aibo mirrors `~/.opencode/memory/`. On macOS that
doesn't exist (only `~/.opencode/bin`), so the script probes `~/.opencode/memory`,
`~/.config/opencode/memory`, `~/.local/share/opencode/memory` and skips quietly
if none exist. Same for Claude Code memory: `~/.claude/projects/*/memory/` is the
real location (one dir exists here, currently empty) — nothing is fabricated.

**Filename collisions with aibo.** Claude Code names are `uuid8 + start minute`,
so a cross-machine collision would need the same uuid4 prefix *and* the same
start minute — not a real risk. OpenCode slugs are the plausible case (aibo's
current opencode emits `chat-xxxxx`, this Mac emits `adjective-noun`; an upgrade
could align them). Policy: never overwrite a file this script can't recognise as
its own — a file whose first line matches and whose bytes are a prefix of the new
render is a grown transcript (ours, updated in place); anything else is written
to `<name>-aibo-mac.md` with a warning.

## Idempotency

Two layers, because it runs every 15 minutes:

1. A `(mtime_ns, size)` index per source file (`~/Library/Application
   Support/sync-ai-sessions/state.json`, deliberately outside both git repos)
   short-circuits parsing unchanged transcripts.
2. Even when it does render, the file is only written if the **bytes** differ.
   Byte comparison, not text: transcripts contain bare `\r` from curl progress
   bars, and `read_text()` would normalise it to `\n` while `write_text()`
   wouldn't — which made every run see a phantom diff and rewrite the file.

`--force` re-renders everything and still reports `0 updated` when nothing
actually changed. If no file changed, git isn't touched at all.

## Logs

`/tmp/sync-ai-sessions.log` (timestamped, flushed — same convention as
`cptr-input/desktop/desktop_server.py` and `mcp-tools`).
