#!/usr/bin/env bash

# On a Mac where this job is a named app (Personal Agent's bin/pa-app; see personal-agent/systems/APPS.md),
# don't create a second, unnamed LaunchAgent: update the app instead.
if [ -d "$HOME/Applications/Personal Agent/AI Session Sync.app" ]; then
  echo "\"AI Session Sync\" is managed by pa-app on this Mac: edit the code in place, or run"
  echo "  personal-agent/bin/pa-app rebuild \"AI Session Sync\"   (see personal-agent/systems/APPS.md)"; exit 0
fi
# sync-ai-sessions installer. Linux: systemd --user timer (below). macOS/launchd: Creates the venv, wires a LaunchAgent that fires every
# 15 minutes (com.sandesh.sync-ai-sessions), and runs it once so you can see the
# result immediately. Idempotent — safe to re-run after a `git pull`.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(uname -s)" = "Linux" ]; then
  # Linux: a systemd --user timer running this folder's script directly (stdlib only, no venv).
  # git commit/push is not done here: sync-repos and the nightly backup.sh push ai-memory.
  # Not sudo: user units live in ~/.config/systemd/user.
  REPO="$HOME/Documents/personal/ai-memory"
  [ -d "$REPO/.git" ] || { echo "missing $REPO, clone it first: git clone git@github.com:sandeshkini/ai-memory \"$REPO\""; exit 1; }
  UNIT_DIR="$HOME/.config/systemd/user"; mkdir -p "$UNIT_DIR" "$HOME/scripts"
  # ~/scripts/sync-ai-sessions.py is the path older docs and habits use; keep it as a link here.
  ln -sfn "$HERE/sync_ai_sessions.py" "$HOME/scripts/sync-ai-sessions.py"
  cat > "$UNIT_DIR/sync-ai-sessions.service" <<EOF2
[Unit]
Description=Sync Claude Code + OpenCode sessions and memories into ai-memory

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $HERE/sync_ai_sessions.py
EOF2
  cat > "$UNIT_DIR/sync-ai-sessions.timer" <<'EOF2'
[Unit]
Description=Run sync-ai-sessions every 15 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF2
  systemctl --user daemon-reload
  systemctl --user enable --now sync-ai-sessions.timer
  systemctl --user start sync-ai-sessions.service
  journalctl --user -u sync-ai-sessions.service -n 3 --no-pager -o cat
  exit 0
fi
if [ "$(uname -s)" != "Darwin" ]; then
  echo "unsupported OS: $(uname -s)"; exit 1
fi

REPO="$HOME/Documents/personal/ai-memory"; [ -d "$REPO/.git" ] || { [ -d "$HOME/Documents/ai-memory/.git" ] && REPO="$HOME/Documents/ai-memory"; }
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
