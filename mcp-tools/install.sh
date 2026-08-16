#!/usr/bin/env bash
# Host install for mcp-tools (no Docker) — for a machine that shares aibo's artifacts
# board + ntfy bus (both singletons, stay on aibo) but needs its own local MCP endpoint so
# ITS cptr/Claude Code/OpenCode can reach publish_artifact + notify at 127.0.0.1:8009/mcp,
# same as on aibo. Talks to aibo's PUBLIC artifacts/ntfy URLs instead of the docker-internal
# service names (there's no compose network here).
#
# Requires the same secrets aibo's agent-tools/.env already has (same shared board/bus):
#   PUBLISH_TOKEN=... NTFY_TOKEN=... ./install.sh
# NTFY_TOPIC defaults to "aibo" (documented everywhere as the shared topic; override if wrong).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "this installer is macOS-only (launchd); adapt for systemd --user on Linux"; exit 1
fi
: "${PUBLISH_TOKEN:?PUBLISH_TOKEN required — same value as aibo's agent-tools/.env}"
: "${NTFY_TOKEN:?NTFY_TOKEN required — same value as aibo's agent-tools/.env}"
NTFY_TOPIC="${NTFY_TOPIC:-aibo}"
SOURCE_LABEL="${SOURCE_LABEL:-cptr}"
# Which machine this is (shows on the board + is filterable) — macOS actually knows its own
# name, unlike a Docker container, so auto-detect instead of forcing a manual value every time.
# Override with COMPUTER_LABEL=... if you want something other than the System Settings name
# (e.g. to match the canonical short names in machines.md, like "aibo-mac").
COMPUTER_LABEL="${COMPUTER_LABEL:-$(scutil --get ComputerName 2>/dev/null || hostname -s)}"

echo "== venv + deps =="
command -v uv >/dev/null || { echo "uv required (same tool cptr installs via) — https://docs.astral.sh/uv/"; exit 1; }

# Deploy to ~/.local/share/mcp-tools rather than running in-place from $HERE. Files under
# ~/Documents can carry a com.apple.provenance xattr (stamped by some write paths, AI-agent
# tools included — it re-stamps itself even after `xattr -d`) that silently blocks a
# launchd-spawned process from opening the file/venv at all: the process starts, never prints a
# line, never binds the port, and never exits either — no error surfaced anywhere obvious. Same
# root cause + same fix as cptr-watchdog's install.sh (see its comment for the fuller writeup).
# The repo copy stays the source of truth; re-run install.sh after editing server.py.
DEPLOY_DIR="$HOME/.local/share/mcp-tools"
mkdir -p "$DEPLOY_DIR"
cp "$HERE/server.py" "$DEPLOY_DIR/server.py"
xattr -d com.apple.provenance "$DEPLOY_DIR/server.py" 2>/dev/null || true
if [ ! -d "$DEPLOY_DIR/.venv" ]; then
  uv venv --python 3.10 "$DEPLOY_DIR/.venv"
fi
uv pip install --python "$DEPLOY_DIR/.venv/bin/python" -q "mcp[cli]>=1.9,<2"

echo "== launchd service =="
PLIST="$HOME/Library/LaunchAgents/com.sandesh.mcp-tools.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.sandesh.mcp-tools</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DEPLOY_DIR/.venv/bin/python</string>
    <string>$DEPLOY_DIR/server.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DEPLOY_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOST</key><string>127.0.0.1</string>
    <key>PORT</key><string>8009</string>
    <key>ARTIFACTS_API</key><string>https://push.artifacts.kingdomofluna.com/api/publish</string>
    <key>PUBLIC_BASE</key><string>https://artifacts.kingdomofluna.com</string>
    <key>PUBLISH_TOKEN</key><string>$PUBLISH_TOKEN</string>
    <key>SOURCE_LABEL</key><string>$SOURCE_LABEL</string>
    <key>COMPUTER_LABEL</key><string>$COMPUTER_LABEL</string>
    <key>NTFY_URL</key><string>https://ntfy.kingdomofluna.com</string>
    <key>NTFY_TOPIC</key><string>$NTFY_TOPIC</string>
    <key>NTFY_TOKEN</key><string>$NTFY_TOKEN</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>StandardOutPath</key><string>/tmp/mcp-tools.log</string>
  <key>StandardErrorPath</key><string>/tmp/mcp-tools.log</string>
</dict></plist>
PL
chmod 600 "$PLIST"
launchctl bootout "gui/$(id -u)/com.sandesh.mcp-tools" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo
echo "computer label: $COMPUTER_LABEL  (override with COMPUTER_LABEL=... if that's not what you want)"
echo "started (binds 127.0.0.1:8009 only — HOST override keeps it off the LAN). verify:"
echo "  curl -s http://127.0.0.1:8009/mcp"
echo "  tail -f /tmp/mcp-tools.log"
echo
echo "next: wire it into Claude Code + OpenCode's own MCP config (cptr doesn't forward its"
echo "tool_servers to the CLIs) — see ~/Documents/aibo-server/Services/cptr/README.md § MCP integrations."
