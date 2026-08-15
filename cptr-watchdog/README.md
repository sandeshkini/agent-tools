# cptr-watchdog

Periodic self-heal for cptr's service supervisor, on any machine running cptr.

**Why this exists.** `KeepAlive` (launchd) / `Restart=always` (systemd) only relaunch the process
while its service definition still exists. Neither covers the service being deregistered or
disabled entirely — an unloaded launchd job, a disabled systemd unit. That's exactly what happened
on aibo-mac on 2026-08-15: `com.cptr.run` was cleanly shut down and its launchd job removed
outright (confirmed: not a reboot, not `cptr-input`'s installer, cause otherwise unconfirmed).
`launchctl kickstart` on a job that no longer exists just errors — there's nothing to kick. cptr is
the primary way this machine is driven, so it needs to come back on its own regardless of *why* it
went away.

## What it does

Every 60s, `watchdog.sh`:
1. Checks whether cptr's service definition is still registered (`launchctl print` / `systemctl
   --user is-enabled`). If not, re-registers it (`launchctl bootstrap` / `systemctl --user enable
   --now`).
2. If registered, checks the HTTP health endpoint. If it doesn't answer, restarts the service
   (`launchctl kickstart -k` / `systemctl --user restart`).

Logs every action taken (not every check) to `~/Library/Logs/cptr-watchdog.log` (macOS) or
`~/.local/state/cptr-watchdog.log` (Linux). A quiet log is a healthy cptr.

## Install

```bash
cd cptr-watchdog
./install.sh
```

OS-detected — macOS gets a `launchd` `StartInterval` job (`com.cptr.watchdog`), Linux gets a
systemd `--user` timer (`cptr-watchdog.timer` + `.service`). Idempotent; safe to re-run.

## Port

Defaults: macOS `8000` (aibo-mac's `cptr` port), Linux `8899` (aibo's `--port` flag — see
`aibo-server/Services/cptr/README.md`). Override with `CPTR_WATCHDOG_PORT` if your install differs
— set it as an `EnvironmentVariables` entry in the plist, or `Environment=` in the systemd service.

## Uninstall

```bash
# macOS
launchctl bootout gui/$(id -u)/com.cptr.watchdog
rm ~/Library/LaunchAgents/com.cptr.watchdog.plist

# Linux
systemctl --user disable --now cptr-watchdog.timer
rm ~/.config/systemd/user/cptr-watchdog.{service,timer}
systemctl --user daemon-reload
```
