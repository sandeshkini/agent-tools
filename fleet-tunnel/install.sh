#!/usr/bin/env bash
# fleet-tunnel: one job that holds this machine's SSH tunnels to aibo-linux, separate from cos-node.
# Decided by Sandesh on 2026-10-07 (D5). Today only aibo-mac needs it, for two forwards:
#   127.0.0.1:8012 -> aibo-linux 127.0.0.1:8012   shared memory (Graphiti MCP)
#   127.0.0.1:8791 -> aibo-linux 127.0.0.1:8790   aibo-linux's cos node (the Chief's hub dials it)
#
# It logs in with its own key (default ~/.ssh/cos_tunnel_ed25519). On aibo-linux that key is allowed
# to open exactly these tunnels and nothing else (D9; see authorize.sh). Sandesh's own key stays
# unrestricted.
#
# The job never reads anything under ~/Documents. The ssh config and the pinned host key are copied
# to ~/.ssh/fleet-tunnel/ at install time, because a launchd job reading files under ~/Documents runs
# into macOS privacy prompts. Re-run install.sh after the fleet ssh config changes.
#
#   fleet-tunnel/install.sh                 install / update (macOS LaunchAgent, Linux systemd --user)
#   fleet-tunnel/install.sh --check         status: job loaded? ports listening? memory answering?
#   fleet-tunnel/install.sh --uninstall     remove the job (cos-node --tunnel flags can take over again)
# Env: FLEET_TUNNEL_KEY, FLEET_TUNNEL_FORWARDS ("8012:8012 8791:8790"), FLEET_TUNNEL_HOST (aibo-linux)
# Docs: aibo-server/infrastructure/agents/tools.md#tunnels
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
AT="$(cd "$HERE/.." && pwd)"
HOSTALIAS="${FLEET_TUNNEL_HOST:-aibo-linux}"
KEY="${FLEET_TUNNEL_KEY:-$HOME/.ssh/cos_tunnel_ed25519}"
FORWARDS="${FLEET_TUNNEL_FORWARDS:-8012:8012 8791:8790}"
DIR="$HOME/.ssh/fleet-tunnel"
LABEL="com.sandesh.fleet-tunnel"
UNIT="fleet-tunnel.service"

# The fleet ssh folder: the aibo-server checkout (same path on every machine since 2026-10-07).
FLEET="${FLEET_SSH_DIR:-$HOME/Documents/personal/aibo-server/infrastructure/ssh}"

status() {
  local p ok=0
  for f in $FORWARDS; do
    p="${f%%:*}"
    if (command -v lsof >/dev/null && lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1) || \
       (command -v ss >/dev/null && ss -ltn "sport = :$p" | grep -q LISTEN); then
      echo "  127.0.0.1:$p listening"
    else echo "  127.0.0.1:$p NOT listening"; ok=1; fi
  done
  for f in $FORWARDS; do [ "${f##*:}" = 8012 ] || continue
    curl -s -m 3 "http://127.0.0.1:${f%%:*}/health" >/dev/null && echo "  memory answers through the tunnel" || { echo "  memory does not answer on :${f%%:*}"; ok=1; }
  done
  return $ok
}

case "${1:-}" in
--check)
  if [ "$(uname -s)" = Darwin ]; then launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E 'state|pid' | head -2 || echo "  $LABEL not loaded"
  else systemctl --user is-active "$UNIT" || true; fi
  status; exit $? ;;
--uninstall)
  if [ "$(uname -s)" = Darwin ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
  else
    systemctl --user disable --now "$UNIT" 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/$UNIT"; systemctl --user daemon-reload
  fi
  echo "removed $LABEL / $UNIT (config in $DIR kept)"; exit 0 ;;
"") ;;
*) echo "usage: $0 [--check|--uninstall]"; exit 2 ;;
esac

[ -f "$KEY" ] || { echo "no key at $KEY. Make one: ssh-keygen -t ed25519 -N '' -C cos-tunnel -f $KEY"; exit 1; }
[ -f "$FLEET/config" ] || { echo "no fleet ssh config at $FLEET (aibo-server checkout?)"; exit 1; }

# 1. a private copy of the host block + pinned host key, outside ~/Documents
mkdir -p "$DIR"; chmod 700 "$DIR"
awk -v h="$HOSTALIAS" '
  /^Host[ \t]/ { keep = ($2 == h) }
  keep' "$FLEET/config" | sed -e "s#UserKnownHostsFile .*#UserKnownHostsFile $DIR/known_hosts#" > "$DIR/config"
grep -q "^Host $HOSTALIAS\$" "$DIR/config" || { echo "no 'Host $HOSTALIAS' in $FLEET/config"; exit 1; }
cat >> "$DIR/config" <<CFG
    IdentityFile $KEY
    IdentitiesOnly yes
    BatchMode yes
    ExitOnForwardFailure yes
    ServerAliveInterval 15
    ServerAliveCountMax 3
    StrictHostKeyChecking yes
CFG
grep -E "^$HOSTALIAS " "$FLEET/known_hosts" > "$DIR/known_hosts"
[ -s "$DIR/known_hosts" ] || { echo "no pinned key for $HOSTALIAS in $FLEET/known_hosts"; exit 1; }
chmod 600 "$DIR/config" "$DIR/known_hosts"

ARGS=(/usr/bin/ssh -F "$DIR/config" -N)
for f in $FORWARDS; do ARGS+=(-L "127.0.0.1:${f%%:*}:127.0.0.1:${f##*:}"); done
ARGS+=("$HOSTALIAS")

# 2. the job
if [ "$(uname -s)" = Darwin ]; then
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo '<plist version="1.0"><dict>'
    echo "  <key>Label</key><string>$LABEL</string>"
    echo '  <key>ProgramArguments</key><array>'
    for a in "${ARGS[@]}"; do echo "    <string>$a</string>"; done
    echo '  </array>'
    echo '  <key>RunAtLoad</key><true/>'
    echo '  <key>KeepAlive</key><true/>'
    echo '  <key>ThrottleInterval</key><integer>10</integer>'
    echo "  <key>StandardErrorPath</key><string>$HOME/Library/Logs/fleet-tunnel.log</string>"
    echo '</dict></plist>'
  } > "$PLIST"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
else
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/$UNIT" <<U
[Unit]
Description=fleet-tunnel: SSH forwards to $HOSTALIAS ($FORWARDS)
After=network-online.target

[Service]
ExecStart=${ARGS[*]}
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
U
  systemctl --user daemon-reload; systemctl --user enable --now "$UNIT"
fi
sleep 4
echo "installed. Forwards: $FORWARDS via $HOSTALIAS, key $KEY"
status || { echo "not up yet: see ~/Library/Logs/fleet-tunnel.log (macOS) or journalctl --user -u $UNIT"; exit 1; }
