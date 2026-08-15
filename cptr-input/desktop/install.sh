#!/usr/bin/env bash
# desktop-streaming installer (macOS only — probe_display.py/inject.py use Quartz).
# Creates the venv, installs deps, and wires a launchd LaunchAgent
# (com.sandesh.agent-desktop) so the streaming server survives login/crash.
# Idempotent — safe to re-run after a `git pull`.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "unsupported OS: desktop streaming is macOS-only right now (Quartz)"; exit 1
fi

echo "== venv + deps =="
command -v uv >/dev/null || { echo "uv required (same tool cptr installs via) — https://docs.astral.sh/uv/"; exit 1; }
if [ ! -d "$HERE/.venv" ]; then
  uv venv --python 3.10 "$HERE/.venv"
fi
uv pip install --python "$HERE/.venv/bin/python" -q fastapi "uvicorn[standard]" \
  pyobjc-framework-Quartz pyobjc-framework-ApplicationServices

echo "== launchd service =="
PLIST="$HOME/Library/LaunchAgents/com.sandesh.agent-desktop.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.sandesh.agent-desktop</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HERE/.venv/bin/python</string>
    <string>$HERE/desktop_server.py</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>StandardOutPath</key><string>/tmp/agent-desktop.log</string>
  <key>StandardErrorPath</key><string>/tmp/agent-desktop.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/com.sandesh.agent-desktop" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo
echo "started. verify:"
echo "  curl -s -o /dev/null -w '%{http_code}\\n' http://127.0.0.1:38218/"
echo "  tail -f /tmp/agent-desktop.log"
echo
echo "GOTCHA (only if you swap back to a system/Xcode python3 -m venv instead of uv's managed"
echo "  one above): the log will show 'Fatal Python error: init_import_site ... PermissionError:"
echo "  Operation not permitted' reading .venv/pyvenv.cfg — macOS TCC blocking launchd from"
echo "  reading anything under ~/Documents for that specific interpreter. uv's python (used by"
echo "  this installer) doesn't hit it. If it recurs: System Settings > Privacy & Security >"
echo "  Full Disk Access > add the real interpreter binary (not the venv symlink), i.e. the"
echo "  target of: python3 -c \"import os; print(os.path.realpath('.venv/bin/python'))\""
echo
echo "clicks need the cptr-input helper's own Accessibility grant (../install.sh) —"
echo "this service streams video fine without it, input just won't land until granted."
