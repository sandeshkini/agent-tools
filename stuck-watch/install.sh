#!/usr/bin/env bash
# Stuck Watch installer: a per-user LaunchAgent (KeepAlive) that watches for AI-agent commands
# stuck on a macOS permission prompt / unanswered dialog and pushes one ntfy alert per issue.
# Idempotent: re-run after a `git pull` to redeploy. See README.md.
#
#   ./install.sh               install or update (deploy copy, venv, config, LaunchAgent)
#   ./install.sh --check       report what's installed and the last log lines (changes nothing)
#   ./install.sh --uninstall   stop and remove the LaunchAgent + deployed copy (keeps config + log)
#
# ntfy settings (optional; otherwise borrowed from an installed mcp-tools LaunchAgent):
#   NTFY_URL=https://ntfy.example.com NTFY_TOPIC=mytopic NTFY_TOKEN=tk_... ./install.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

CHECK=0; UNINSTALL=0
for a in "$@"; do
  case "$a" in
    --check) CHECK=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n 2,12p "$0"; exit 0 ;;
    *) echo "unknown option: $a"; exit 1 ;;
  esac
done

[ "$(uname -s)" = "Darwin" ] || { echo "macOS only (it watches TCC, launchd and macOS dialogs)"; exit 1; }

UID_=$(id -u)
LABEL="com.sandesh.stuck-watch"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DEPLOY_DIR="$HOME/.local/share/stuck-watch"
CONFIG="$HOME/.config/stuck-watch/config"
LOG="$HOME/Library/Logs/stuck-watch.log"
PA_APP="$HOME/Applications/Personal Agent/Stuck Watch.app"
PA_LABEL="com.sandesh.pa.stuck-watch"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
miss() { printf '  \033[33m•\033[0m %s\n' "$*"; }
loaded() { launchctl print "gui/$UID_/$1" >/dev/null 2>&1; }

check() {
  echo "== status =="
  if [ -d "$PA_APP" ]; then
    ok "managed by pa-app as \"Stuck Watch\" ($PA_LABEL)"
    loaded "$PA_LABEL" && ok "running" || miss "$PA_LABEL not loaded"
  elif loaded "$LABEL"; then
    ok "LaunchAgent $LABEL loaded"
    launchctl print "gui/$UID_/$LABEL" | grep -E '^\s*(state|pid|last exit code) =' | head -3 | sed 's/^[[:space:]]*/    /'
  else
    miss "not installed ($LABEL not loaded)"
  fi
  if [ -f "$CONFIG" ]; then
    ok "config $CONFIG (keys: $(grep -Eo '^[A-Z_]+' "$CONFIG" | tr '\n' ' '))"
  else
    miss "no $CONFIG (defaults; ntfy borrowed from mcp-tools if installed)"
  fi
  if grep -qs '^NTFY_URL=' "$CONFIG" || [ -f "$HOME/Library/LaunchAgents/com.sandesh.mcp-tools.plist" ] \
     || [ -f "$HOME/Library/LaunchAgents/com.sandesh.pa.agent-tools-mcp.plist" ]; then
    ok "ntfy configured"
  else
    miss "ntfy not configured: alerts only go to $LOG"
  fi
  if [ -f "$LOG" ]; then echo "  last log lines ($LOG):"; tail -5 "$LOG" | sed 's/^/    /'; fi
}

if [ $CHECK = 1 ]; then check; exit 0; fi

if [ $UNINSTALL = 1 ]; then
  if [ -d "$PA_APP" ]; then
    echo "\"Stuck Watch\" is managed by pa-app on this Mac: personal-agent/bin/pa-app remove \"Stuck Watch\""; exit 0
  fi
  launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null && ok "stopped $LABEL" || miss "$LABEL was not loaded"
  rm -f "$PLIST" && ok "removed $PLIST"
  rm -rf "$DEPLOY_DIR" && ok "removed $DEPLOY_DIR"
  echo "kept: $CONFIG and $LOG (delete by hand if you want them gone)"
  exit 0
fi

# On a Mac where background jobs are named apps (Personal Agent's bin/pa-app), don't create a
# second, unnamed LaunchAgent: the app runs stuck_watch.py in place from this repo.
if [ -d "$PA_APP" ]; then
  echo "\"Stuck Watch\" is managed by pa-app on this Mac: edit the code in place, then"
  echo "  launchctl kickstart -k gui/$UID_/$PA_LABEL   (see personal-agent/systems/APPS.md)"; exit 0
fi
# Same if mac-apps wrapped this LaunchAgent in a named app: rewriting the plist would undo that.
if plutil -extract ProgramArguments.0 raw "$PLIST" 2>/dev/null | grep -q '\.app/Contents/MacOS/'; then
  echo "$LABEL runs as a named app (agent-tools/mac-apps); not rewriting it. The app runs the deployed copy:"
  echo "  cp \"$HERE/stuck_watch.py\" \"$DEPLOY_DIR/\" && launchctl kickstart -k gui/$UID_/$LABEL"; exit 0
fi

echo "== deploy + venv =="
command -v uv >/dev/null || { echo "uv required — https://docs.astral.sh/uv/ (brew install uv)"; exit 1; }
# Deploy a copy outside ~/Documents: files there can carry a com.apple.provenance xattr that
# blocks a launchd-spawned process from opening them (same reason as mcp-tools/cptr-watchdog).
# The repo copy stays the source of truth; re-run install.sh after editing stuck_watch.py.
mkdir -p "$DEPLOY_DIR"
cp "$HERE/stuck_watch.py" "$DEPLOY_DIR/stuck_watch.py"
xattr -d com.apple.provenance "$DEPLOY_DIR/stuck_watch.py" 2>/dev/null || true
# Stdlib only; the venv pins a known Python (not the Xcode stub) like the repo's other tools.
[ -x "$DEPLOY_DIR/.venv/bin/python" ] || uv venv -q --python 3.12 "$DEPLOY_DIR/.venv"
ok "deployed to $DEPLOY_DIR"

echo "== config =="
mkdir -p "$(dirname "$CONFIG")"
if [ ! -f "$CONFIG" ]; then
  cat > "$CONFIG" <<'CF'
# Stuck Watch config (KEY=VALUE). Environment variables override these. See README.md.
# STUCK_WATCH_AGENTS=claude,opencode,codex,cptr
# STUCK_WATCH_MINUTES=3            # file operations idle this long = stuck
# STUCK_WATCH_OTHER_MINUTES=15     # any other agent command
# STUCK_WATCH_PROMPT_MINUTES=2     # TCC prompt / on-screen dialog unanswered this long
# STUCK_WATCH_REALERT_MINUTES=30
# STUCK_WATCH_HOST=my-mac          # name in messages (default: COMPUTER_LABEL, else ComputerName)
CF
  ok "created $CONFIG"
else
  ok "kept existing $CONFIG"
fi
chmod 600 "$CONFIG"
for k in NTFY_URL NTFY_TOPIC NTFY_TOKEN; do
  v="${!k:-}"
  [ -n "$v" ] || continue
  grep -v "^$k=" "$CONFIG" > "$CONFIG.tmp" || true
  echo "$k=$v" >> "$CONFIG.tmp"; mv "$CONFIG.tmp" "$CONFIG"; chmod 600 "$CONFIG"
  ok "set $k in config"
done

echo "== LaunchAgent =="
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <!-- Generated by agent-tools/stuck-watch/install.sh. Settings live in ~/.config/stuck-watch/config. -->
  <key>ProgramArguments</key>
  <array>
    <string>$DEPLOY_DIR/.venv/bin/python</string>
    <string>$DEPLOY_DIR/stuck_watch.py</string>
  </array>
  <key>WorkingDirectory</key><string>$DEPLOY_DIR</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>/tmp/stuck-watch.out</string>
  <key>StandardErrorPath</key><string>/tmp/stuck-watch.out</string>
</dict></plist>
PL
launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_" "$PLIST"
ok "started $LABEL"
sleep 2
check
echo
echo "alerts log: $LOG   (stdout/stderr: /tmp/stuck-watch.out)"
