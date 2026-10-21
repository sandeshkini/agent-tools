#!/usr/bin/env bash
# machine-health installer (macOS): the one LaunchAgent for stuck-watch + mem-reaper + stale-prompts + docker-hygiene.
# Migrates and removes the old stuck-watch / mem-reaper jobs. Idempotent; re-run after a `git pull`.
#   ./install.sh [--check|--uninstall]      (same as: machine-health install ...)
exec /usr/bin/python3 "$(cd "$(dirname "$0")" && pwd)/machine_health.py" install "$@"
