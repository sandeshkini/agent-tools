# stuck-watch: notice when an agent is silently stuck on macOS

AI agents (Claude Code, OpenCode, Codex, cptr) run their commands from background processes
that can't show macOS dialogs. When macOS wants to ask something, the command just waits,
forever, and the chat looks frozen with no error. Real cases:

- `rm ~/Downloads/<file Chrome downloaded>` hung for hours: the file's `com.apple.macl` tag meant
  macOS wanted to ask whether the agent may touch another app's data, and nobody could see the ask.
- A background app's TCC prompt (Photos, then iCloud Drive) sat on screen unanswered for 14 hours.

`stuck_watch.py` runs as a KeepAlive LaunchAgent and checks every 60 s for:

| Check | How | Default threshold |
|---|---|---|
| **Stuck agent commands** | `ps` tree: processes run by an agent's shell (`sh/zsh/bash -c` below a process named `claude`, `opencode`, `codex`, `cptr`) that have stopped using CPU. The deepest process of a pipeline is reported. | file operations (`rm mv cp xattr cat ls find python …`) idle 3 min; any other command 15 min |
| **Unanswered TCC prompts** | `/usr/bin/log stream` on `com.apple.TCC`: an `AUTHREQ_PROMPTING msgID=X` with no `AUTHREQ_RESULT msgID=X` yet. Any app, marked "(agent)" when the request is attributed to an agent process. Prompts from the last hour are picked up at start. | 2 min |
| **TCC denials for agents** | `AUTHREQ_RESULT authValue=0` for a non-preflight request whose `AUTHREQ_ATTRIBUTION` names an agent (by pid or path). | immediately |
| **Dialogs left on screen** | `CGWindowListCopyWindowInfo` (ctypes, no Accessibility or Screen Recording needed): windows owned by `UserNotificationCenter`, `SecurityAgent`, `coreautha`, `tccd`, `universalAccessAuthWarn`, `CoreServicesUIAgent`, `AuthenticationServicesAgent`; a sheet-sized second window in System Settings; any window titled "wants to access / make changes / password" (titles only visible if the process has Screen Recording). | 2 min |

Each distinct issue gets **one** ntfy push, and another one every 30 min while it's still there.
When it goes away, a `CLEARED` line goes in the log and a new occurrence alerts again right away.
Everything is also appended to `~/Library/Logs/stuck-watch.log`. Messages look like:

    Stuck 4m on my-mac: rm ~/Downloads/x.xlsx (waiting on macOS permission?) [claude, pid 4242]
    Permission prompt waiting 12m on my-mac: Plays Weekly wants Photos
    Password prompt waiting in SecurityAgent on my-mac (3m)

Command lines are truncated, `~`-shortened, and anything that looks like a secret
(`--token=…`, `Bearer …`, `user:pass@`, `sk-…`, `ghp_…`, long opaque strings) is replaced by `***`.

**It only reads.** It never kills a process, answers a prompt, or clicks anything. You go to the
Mac (or Screen Sharing) and deal with it.

## Install (any Mac)

```bash
git clone https://github.com/sandeshkini/agent-tools && agent-tools/stuck-watch/install.sh
```

Needs `uv` (`brew install uv`). The installer copies `stuck_watch.py` to
`~/.local/share/stuck-watch/` (outside `~/Documents`, whose provenance xattr can block
launchd-spawned processes), makes a venv, writes `~/.config/stuck-watch/config` if missing,
and loads the LaunchAgent `com.sandesh.stuck-watch`. Re-run after a `git pull` to update.

```bash
stuck-watch/install.sh --check       # status, config keys (not values), last log lines
stuck-watch/install.sh --uninstall   # stop + remove the agent and deployed copy (keeps config/log)
```

On a Mac where background jobs are named apps (personal-agent's `pa-app`), the installer
defers to the "Stuck Watch" app instead of creating a second LaunchAgent.

## Config

Environment variables override `~/.config/stuck-watch/config` (`KEY=VALUE` lines, mode 600).

| Key | Default | |
|---|---|---|
| `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN` | borrowed from an installed mcp-tools LaunchAgent | where pushes go (same bus as mcp-tools' `notify`). Pass them to `install.sh` as env vars to store them in the config. Without any, alerts only go to the log. |
| `STUCK_WATCH_AGENTS` | `claude,opencode,codex,cptr` | process names (argv[0] or one of the first argv words) that count as agents |
| `STUCK_WATCH_MINUTES` | `3` | file operations idle this long are stuck |
| `STUCK_WATCH_OTHER_MINUTES` | `15` | any other agent command |
| `STUCK_WATCH_PROMPT_MINUTES` | `2` | TCC prompt / dialog unanswered this long |
| `STUCK_WATCH_REALERT_MINUTES` | `30` | repeat interval while an issue persists |
| `STUCK_WATCH_INTERVAL` | `60` | seconds between scans |
| `STUCK_WATCH_CPU_EPSILON` | `0.05` | CPU seconds per interval that still count as idle |
| `STUCK_WATCH_IGNORE` | `sleep`, `tail -f`, `less`, `ssh`, `tmux`, `log stream`, … | regex on the command line (program as basename) for things meant to sit idle |
| `STUCK_WATCH_DIALOG_OWNERS` | list above | window owners that count as dialogs |
| `STUCK_WATCH_HOST` | `COMPUTER_LABEL` (as mcp-tools), else the Mac's ComputerName | name used in messages |
| `STUCK_WATCH_TITLE` | `Stuck Watch` | ntfy title |
| `STUCK_WATCH_TCC`, `STUCK_WATCH_WINDOWS` | `1` | set `0` to turn a check off |
| `STUCK_WATCH_LOG`, `STUCK_WATCH_STATE` | `~/Library/Logs/stuck-watch.log`, `~/.local/state/stuck-watch/state.json` | |
| `STUCK_WATCH_DRY_RUN` | `0` | `1` = log "would notify" instead of pushing (also `--dry-run`) |

Try it by hand: `python3 stuck_watch.py --once --dry-run` prints what one pass sees.

## Testing it

Fake a stuck agent command (a `cat` on a FIFO blocks forever with zero CPU, like a TCC hang):

```bash
mkfifo /tmp/sw-fifo
bash -c 'exec -a claude bash -c "/bin/zsh -c \"cat /tmp/sw-fifo; true\""' &
STUCK_WATCH_MINUTES=1 STUCK_WATCH_INTERVAL=15 STUCK_WATCH_TITLE="Stuck Watch test" python3 stuck_watch.py
# one push after ~1 min, no repeats; then: pkill -f sw-fifo; rm /tmp/sw-fifo
```

## Limits

- **Heuristic.** "Idle with no CPU" can't tell a TCC/macl hang from a command waiting on the
  network, a lock, or stdin (a `git` or `sudo` password prompt, say). It says what it sees and
  guesses why. Servers an agent starts on purpose are skipped when they listen on a TCP port;
  other long idle commands only alert after 15 min; add patterns to `STUCK_WATCH_IGNORE` for more.
- **macl / App Data hangs usually don't log a TCC prompt**, so only the stuck-command check
  catches them. Conversely a TCC prompt for a process that isn't an agent (a background app)
  is reported by the TCC and window checks.
- Agents are recognised by process name. A command run outside an agent's shell (e.g. an
  MCP server the agent spawned directly) isn't watched.
- Window owners for prompts change between macOS versions; without Screen Recording window
  titles are empty, so the System Settings sheet detection is a size heuristic.
- The unified-log check misses prompts raised more than an hour before the watcher started
  (the window check still sees them if they're on screen).
- macOS only. It needs no TCC permission itself: it reads `ps`, the unified log, the window
  list, and files outside protected folders.
