# sync-ai-sessions

Exports every Claude Code and OpenCode conversation on a machine, plus both tools' memory files,
into `~/Documents/personal/ai-memory/` (private repo `github.com/sandeshkini/ai-memory`) every 15
minutes. That includes every cptr chat, because cptr runs Claude Code and OpenCode. It's the only
long-term copy of the transcripts: Claude Code deletes its own after about 30 days.

**One script, every machine:** `sync_ai_sessions.py` (stdlib only). Since 2026-10-07 aibo-linux runs
this file too; the old copy in aibo-server `infrastructure/scripts/` is gone.

| Machine | How it runs | Who pushes |
|---|---|---|
| aibo-linux | systemd `--user` `sync-ai-sessions.timer`, made by `./install.sh`. `~/scripts/sync-ai-sessions.py` links here | `sync-repos` and the nightly `backup.sh` |
| aibo-mac | pa-app named app "AI Session Sync" (`com.sandesh.pa.ai-session-sync`) running `mac_wrapper.py` | `mac_wrapper.py`, right after each run |

`mac_wrapper.py` runs the script **in-process** (not as a second Python): under launchd, a nested
Python opening `~/Documents` hangs forever on macOS's privacy (TCC) check. See its docstring.

Shared memory (Graphiti, `../memory/`) is separate: it holds only what agents choose to record, and
its own nightly export writes `ai-memory/knowledge/`. Chats are not fed into it.

## Install / run

```bash
./install.sh                                    # Linux: timer; macOS: LaunchAgent (skipped if the named app exists)
python3 sync_ai_sessions.py                     # run once by hand
AI_MEMORY_DIR=/tmp/copy python3 sync_ai_sessions.py   # try it against a copy of ai-memory
systemctl --user list-timers sync-ai-sessions.timer   # Linux
tail -f /tmp/sync-ai-sessions.log                     # macOS
```

## What it writes

| Source | Becomes |
|---|---|
| `~/.claude/projects/*/<uuid>.jsonl` | `sessions/<uuid8>-<YYYYMMDD>-<HHMM>.md` |
| `~/.claude/projects/*/<uuid>/subagents/agent-*.jsonl` | `sessions/subagents/<parent8>-sub-<agent8>-<stamp>.md` |
| `~/.claude/projects/*/memory/` | `memories/claude/<project dir name>/` (rsync mirror) |
| `~/.local/share/opencode/opencode.db` (read-only) | `sessions/oc-<slug>-<last 8 of session id>.md` |
| `~/.opencode/memory/` | `memories/opencode/` |

Project dirs and sessions are discovered at run time. A transcript is only rewritten when its
source is newer than the export, so a run with nothing new is cheap.

## Decisions worth knowing

**Which OpenCode sessions are skipped.** Only claude-monitor's leftovers: that retired tool ran a
headless terminal summarizer through OpenCode and left ~13,000 sessions on aibo-linux, each starting
with "Summarize this terminal session in one short line". Everything else is exported, including
subagent sessions. Until 2026-10-07 the rule was `agent != 'build'`, which also dropped every real
chat, because `build` is OpenCode's default agent: no cptr or TUI OpenCode chat was ever saved.
`SYNC_EXCLUDE_AGENTS=a,b` adds an agent-name exclusion if one is ever needed.

**OpenCode file names carry the session id.** Slugs repeat (OpenCode reuses a small pool, and all
machines write into one folder), so the old `oc-<slug>.md` names overwrote each other across
sessions and machines. A run removes an old `oc-<slug>.md` only when it is the same session's
transcript (its text is the start of the new export); any other machine's file is left alone.

**Where the repo is.** `~/Documents/personal/ai-memory` first, then the old `~/Documents/ai-memory`
as a fallback; `$AI_MEMORY_DIR` overrides both.
