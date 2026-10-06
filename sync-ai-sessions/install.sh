#!/usr/bin/env bash

# On a Mac where this job is a named app (Personal Agent's bin/pa-app; see personal-agent/systems/APPS.md),
# don't create a second, unnamed LaunchAgent: update the app instead.
if [ -d "$HOME/Applications/Personal Agent/AI Session Sync.app" ]; then
  echo "\"AI Session Sync\" is managed by pa-app on this Mac: edit the code in place, or run"
  echo "  personal-agent/bin/pa-app rebuild \"AI Session Sync\"   (see personal-agent/systems/APPS.md)"; exit 0
fi
# sync-ai-sessions installer (macOS/launchd — aibo runs the same job as a
# systemd --user timer). Creates the venv, wires a LaunchAgent that fires every
# 15 minutes (com.sandesh.sync-ai-sessions), and runs it once so you can see the
# result immediately. Idempotent — safe to re-run after a `git pull`.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "this installer is macOS-only (launchd); on Linux use a systemd --user timer like aibo's"; exit 1
fi

REPO="$HOME/Documents/ai-memory"; [ -d "$HOME/Documents/personal/ai-memory/.git" ] && REPO="$HOME/Documents/personal/ai-memory"
if [ ! -d "$REPO/.git" ]; then
  echo "missing $REPO — clone it first:"
  echo "  git clone https://github.com/sandeshkini/ai-memory \"$REPO\""
  exit 1
fi

echo "== venv =="
# No third-party deps (stdlib only: json/sqlite3/subprocess) — the venv exists to
# match the other host services here AND because launchd + a system/Xcode python
# trips macOS TCC when reading ~/Documents (see cptr-input/desktop/install.sh).
command -v uv >/dev/null || { echo "uv required (same tool cptr installs via) — https://docs.astral.sh/uv/"; exit 1; }
if [ ! -d "$HERE/.venv" ]; then
  uv venv --python 3.10 "$HERE/.venv"
fi

echo "== launchd job (every 15 min) =="
PLIST="$HOME/Library/LaunchAgents/com.sandesh.sync-ai-sessions.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.sandesh.sync-ai-sessions</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HERE/.venv/bin/python</string>
    <string>$HERE/mac_wrapper.py</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/sync-ai-sessions.log</string>
  <key>StandardErrorPath</key><string>/tmp/sync-ai-sessions.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/com.sandesh.sync-ai-sessions" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo
echo "installed. verify:"
echo "  launchctl print gui/$(id -u)/com.sandesh.sync-ai-sessions | head -20"
echo "  launchctl kickstart -k gui/$(id -u)/com.sandesh.sync-ai-sessions   # run now"
echo "  tail -f /tmp/sync-ai-sessions.log"
echo
echo "manual run (export only, no commit/push):  $HERE/.venv/bin/python $HERE/sync_ai_sessions.py"
