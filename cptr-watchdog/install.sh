#!/usr/bin/env bash
# cptr-watchdog installer. Detects the OS and schedules watchdog.sh to run
# every 60s (launchd StartInterval / systemd timer). Idempotent.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/watchdog.sh"
chmod +x "$SCRIPT"

case "$(uname -s)" in
Darwin)
  echo "== macOS =="
  # Deploy a copy to ~/.local/bin rather than pointing launchd at the repo
  # checkout directly. Files under ~/Documents can carry a com.apple.provenance
  # xattr (stamped by some write paths, AI-agent tools included, and it
  # re-stamps itself even after `xattr -d`) that blocks a launchd-spawned
  # process from opening the file at all -- "Operation not permitted", no
  # visible error beyond a launchd exit code 126. ~/.local/bin is outside that
  # scope and is already where cptr/newt themselves run from successfully
  # under launchd on this machine, so this mirrors proven-working precedent.
  # The repo copy stays the source of truth; re-run install.sh after editing it.
  mkdir -p "$HOME/.local/bin"
  DEPLOYED="$HOME/.local/bin/cptr-watchdog.sh"
  cp "$SCRIPT" "$DEPLOYED"
  chmod +x "$DEPLOYED"
  xattr -d com.apple.provenance "$DEPLOYED" 2>/dev/null || true

  PLIST="$HOME/Library/LaunchAgents/com.cptr.watchdog.plist"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>com.cptr.watchdog</string>
	<key>ProgramArguments</key><array><string>$DEPLOYED</string></array>
	<key>StartInterval</key><integer>60</integer>
	<key>RunAtLoad</key><true/>
	<key>StandardOutPath</key><string>$HOME/Library/Logs/cptr-watchdog.log</string>
	<key>StandardErrorPath</key><string>$HOME/Library/Logs/cptr-watchdog.log</string>
</dict>
</plist>
PL
  launchctl bootout "gui/$(id -u)/com.cptr.watchdog" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "installed — checks every 60s, log at ~/Library/Logs/cptr-watchdog.log"
  echo "verify: launchctl print gui/\$(id -u)/com.cptr.watchdog | grep state"
  ;;

Linux)
  echo "== Linux =="
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/cptr-watchdog.service" <<UN
[Unit]
Description=cptr-watchdog (one-shot health check + self-heal)

[Service]
Type=oneshot
ExecStart=$SCRIPT
Environment=CPTR_WATCHDOG_LOG=%h/.local/state/cptr-watchdog.log
UN
  cat > "$HOME/.config/systemd/user/cptr-watchdog.timer" <<UN
[Unit]
Description=Run cptr-watchdog every 60s

[Timer]
OnBootSec=30
OnUnitActiveSec=60

[Install]
WantedBy=timers.target
UN
  systemctl --user daemon-reload
  systemctl --user enable --now cptr-watchdog.timer
  echo "installed — checks every 60s, log at ~/.local/state/cptr-watchdog.log"
  echo "verify: systemctl --user status cptr-watchdog.timer"
  ;;

*)
  echo "unsupported OS: $(uname -s)"; exit 1;;
esac
