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

echo "== venv + deps =="
command -v uv >/dev/null || { echo "uv required (same tool cptr installs via) — https://docs.astral.sh/uv/"; exit 1; }
if [ ! -d "$HERE/.venv" ]; then
  uv venv --python 3.10 "$HERE/.venv"
fi
uv pip install --python "$HERE/.venv/bin/python" -q "mcp[cli]>=1.9,<2"

echo "== launchd service =="
PLIST="$HOME/Library/LaunchAgents/com.sandesh.mcp-tools.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.sandesh.mcp-tools</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HERE/.venv/bin/python</string>
    <string>$HERE/server.py</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOST</key><string>127.0.0.1</string>
    <key>PORT</key><string>8009</string>
    <key>ARTIFACTS_API</key><string>https://apps.kingdomofluna.com/artifacts/api/publish</string>
    <key>PUBLIC_BASE</key><string>https://apps.kingdomofluna.com</string>
    <key>PUBLISH_TOKEN</key><string>$PUBLISH_TOKEN</string>
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
echo "started (binds 127.0.0.1:8009 only — HOST override keeps it off the LAN). verify:"
echo "  curl -s http://127.0.0.1:8009/mcp"
echo "  tail -f /tmp/mcp-tools.log"
echo
echo "next: wire it into Claude Code + OpenCode's own MCP config (cptr doesn't forward its"
echo "tool_servers to the CLIs) — see ~/Documents/aibo-server/Services/cptr/README.md § MCP integrations."
