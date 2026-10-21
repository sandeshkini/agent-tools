# machine-health

One watcher for a machine that agents work on. One CLI, one config, one log, one LaunchAgent.

```
machine-health status                       # each check: running? last run + result; the job; old jobs still loaded?
machine-health run --once --dry-run         # one pass of every check, changes and sends nothing
machine-health run --once --check mem-reaper
machine-health install [--check|--uninstall]
```
`machine-health` is on PATH through `registry.toml [bin]`. `install` is idempotent; re-run after a `git pull`.

| check | what it does | every |
|---|---|---|
| **stuck-watch** | agent commands stuck on a macOS permission prompt, unanswered TCC prompts and password dialogs; one ntfy push per issue, backing off. Read-only. Details and keys: [STUCK-WATCH.md](STUCK-WATCH.md) | 60 s |
| **mem-reaper** | kills agent-browser daemons nothing can reach any more (or idle 2 h), closes Agent Chrome (CDP :9333) tabs unchanged for 3 h (not login/auth/SSO pages, nothing while a routine runs), kills Agent Chrome renderers over 3 GB; at swap > 20 GB or critical pressure it cleans at once and pushes one ntfy with the top 5 processes (at most every 3 h). Never touches Sandesh's own Chrome | 2 min (full clean 30 min) |
| **stale-prompts** | a `universalAccessAuthWarn` permission prompt open over an hour for an app that tccd already granted: ends that process. Never presses Deny (Deny can record a refusal). If the TCC log doesn't show the grant, it leaves the prompt alone | 5 min |
| **idle-simulators** | any iOS Simulator booted over 2 h (age of its `launchd_sim`) with no xcodebuild/XCBBuildService/xctest/simctl running: `xcrun simctl shutdown all`, logged, one alert. Threshold `MACHINE_HEALTH_SIM_IDLE_S` (default 7200) | 10 min |
| **docker-hygiene** | **report only**: dangling images, cos-hub tags beyond the newest 3, `cosreltest*`, old lifeos-app/web tags. `*rollback*` images are never listed. It has no delete code at all | daily |

Config `~/.config/machine-health/config` (KEY=VALUE, see `config.example`; env overrides; the old stuck-watch config is migrated and still read).
Log `~/Library/Logs/machine-health.log`. Status `~/.local/state/machine-health/status.json`.

**Install on a Mac:** `install` migrates and removes the old jobs (the "Stuck Watch" app, `com.sandesh.stuck-watch`,
`com.sandesh.mem-reaper`), then, where personal-agent's `pa-app` exists, installs one named app **Machine Health**
(`com.sandesh.pa.machine-health`, KeepAlive; prompts and Login Items say "Machine Health"); otherwise a plain
LaunchAgent `com.sandesh.machine-health` from a copy in `~/.local/share/machine-health`.
**Linux (aibo-linux):** stuck-watch, mem-reaper and stale-prompts watch macOS things, so `install` does nothing there;
`machine-health status` shows them as n/a, and `machine-health run --once --check docker-hygiene` gives the read-only docker report.

Tests: `python3 -m unittest discover -s tests`.
