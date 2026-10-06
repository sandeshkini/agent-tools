#!/usr/bin/env bash
# Installs the nightly memory export on the machine that runs the `memory` container (aibo-linux):
# systemd --user memory-export.timer → memory/export.py → ai-memory/knowledge/<group>/ (git).
# Idempotent. The container itself is part of agent-tools' docker compose (`docker compose up -d memory`).
#   memory/install.sh            install / update the timer
#   memory/install.sh --check    status
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
if [ "${1:-}" = "--check" ]; then
  curl -s -m 3 http://127.0.0.1:8012/health; echo
  systemctl --user list-timers memory-export.timer --no-pager || true
  journalctl --user -u memory-export -n 5 --no-pager || true
  exit 0
fi
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/memory-export.service" <<UNIT
[Unit]
Description=Export shared agent memory to Markdown (ai-memory/knowledge)

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $HERE/export.py --group personal
UNIT
cat > "$UNIT_DIR/memory-export.timer" <<UNIT
[Unit]
Description=Nightly export of shared agent memory (before backup.sh pushes ai-memory)

[Timer]
OnCalendar=*-*-* 01:30
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now memory-export.timer
systemctl --user start memory-export.service
systemctl --user list-timers memory-export.timer --no-pager | head -3
journalctl --user -u memory-export -n 3 --no-pager
