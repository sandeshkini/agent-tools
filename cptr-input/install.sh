#!/usr/bin/env bash
# cptr-input installer. Detects the OS, installs the right backend, wires the
# service, and prints how to grant permission. Idempotent.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OS="$(uname -s)"

case "$OS" in
Darwin)
  echo "== macOS =="
  APP="$HERE/macos/cptr-input.app"
  BIN="$APP/Contents/MacOS/cptr-input"
  if [ ! -x "$BIN" ]; then
    echo "building..."; "$HERE/macos/build.sh"
  fi
  PLIST="$HOME/Library/LaunchAgents/com.sandesh.cptr-input.plist"
  cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.sandesh.cptr-input</string>
  <key>ProgramArguments</key><array><string>$BIN</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/cptr-input.log</string>
  <key>StandardErrorPath</key><string>/tmp/cptr-input.log</string>
</dict></plist>
PL
  launchctl bootout "gui/$(id -u)/com.sandesh.cptr-input" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo
  echo "GRANT ACCESSIBILITY (one time, then it sticks across upgrades):"
  echo "  1. open: System Settings > Privacy & Security > Accessibility"
  echo "  2. add:  $APP"
  echo "  3. restart: launchctl kickstart -k gui/$(id -u)/com.sandesh.cptr-input"
  echo "  verify: python3 -c \"import client; print(client.InputClient().ping())\""
  ;;

Linux)
  echo "== Linux =="
  command -v python3 >/dev/null || { echo "python3 required"; exit 1; }
  # evdev is the only dependency
  if ! python3 -c "import evdev" 2>/dev/null; then
    echo "installing python-evdev..."
    pip3 install --user evdev || python3 -m pip install --user evdev
  fi
  # /dev/uinput access: input group + udev rule (needs sudo once)
  if [ ! -w /dev/uinput ]; then
    echo "granting /dev/uinput access (sudo)..."
    sudo groupadd -f input
    sudo usermod -aG input "$USER"
    echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
      | sudo tee /etc/udev/rules.d/99-cptr-input.rules >/dev/null
    sudo modprobe uinput
    sudo udevadm control --reload-rules && sudo udevadm trigger
    echo "  NOTE: log out/in (or reboot) for the 'input' group to take effect."
  fi
  # systemd --user service
  mkdir -p "$HOME/.config/systemd/user"
  UNIT="$HOME/.config/systemd/user/cptr-input.service"
  cat > "$UNIT" <<UN
[Unit]
Description=cptr-input (Linux uinput daemon)
After=default.target

[Service]
ExecStart=$(command -v python3) $HERE/linux/cptr_input.py
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
UN
  systemctl --user daemon-reload
  systemctl --user enable --now cptr-input.service
  echo
  echo "verify: python3 -c \"import client; print(client.InputClient().ping())\""
  echo "  expect trusted=true once you are in the 'input' group (re-login if not)."
  ;;

*)
  echo "unsupported OS: $OS"; exit 1;;
esac
