#!/usr/bin/env bash
# cptr-watchdog — periodic self-heal for cptr's own service supervisor.
#
# KeepAlive (launchd) / Restart=always (systemd) only cover the process
# crashing while the service definition still exists. They do NOT cover the
# service being deregistered/disabled entirely (unloaded launchd job,
# disabled systemd unit) — which is exactly what happened on aibo-mac on
# 2026-08-15 for an unconfirmed reason (not a reboot, not this machine's own
# cptr-input installer). This script re-registers the service if it's gone,
# and restarts it if it's registered but not answering health checks.
#
# Installed + scheduled by install.sh (launchd StartInterval on macOS,
# systemd timer on Linux) — do not run this manually except to test.
set -euo pipefail

PORT="${CPTR_WATCHDOG_PORT:-}"
LOG="${CPTR_WATCHDOG_LOG:-$HOME/Library/Logs/cptr-watchdog.log}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG"; }
mkdir -p "$(dirname "$LOG")"

case "$(uname -s)" in
Darwin)
  PORT="${PORT:-8000}"
  LABEL="com.cptr.run"
  DOMAIN="gui/$(id -u)"
  PLIST="$HOME/Library/LaunchAgents/com.cptr.run.plist"

  if ! launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    log "[macos] job missing — bootstrapping"
    launchctl bootstrap "$DOMAIN" "$PLIST" 2>>"$LOG"
    exit 0
  fi

  if ! curl -sf -o /dev/null --max-time 5 "http://127.0.0.1:$PORT/"; then
    log "[macos] job loaded but not responding on :$PORT — kickstarting"
    launchctl kickstart -k "$DOMAIN/$LABEL" 2>>"$LOG"
  fi
  ;;

Linux)
  PORT="${PORT:-8899}"
  SERVICE="cptr"

  if ! systemctl --user is-enabled "$SERVICE" >/dev/null 2>&1; then
    log "[linux] unit disabled/missing — enabling + starting"
    systemctl --user enable --now "$SERVICE" 2>>"$LOG"
    exit 0
  fi

  if ! systemctl --user is-active "$SERVICE" >/dev/null 2>&1 || \
     ! curl -sf -o /dev/null --max-time 5 "http://127.0.0.1:$PORT/health"; then
    log "[linux] unit enabled but not healthy on :$PORT — restarting"
    systemctl --user restart "$SERVICE" 2>>"$LOG"
  fi
  ;;

*)
  echo "unsupported OS: $(uname -s)" >&2
  exit 1
  ;;
esac
