#!/usr/bin/env bash
# agent-tools setup: bring this machine to its profile (macOS or Linux). See setup/README.md.
#
#   ./setup.sh --check                  what's in place vs the profile; changes nothing
#   ./setup.sh                          apply what --check says would change (asks first on a terminal)
#   ./setup.sh --only stuck-watch       just one component (comma-separate several)
#   ./setup.sh --rollback named-apps    undo the last apply of a component
#   ./setup.sh --list                   the components
#   ./setup.sh --profile-from my-infra --check      pick this host's profile from a profiles repo
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# A Python 3.9+ that is real: on a Mac without the Command Line Tools, /usr/bin/python3 is a stub
# that pops up an install dialog, so only use it when they're installed.
PY=""
for p in python3.13 python3.12 python3.11 python3.10 python3; do
  c="$(command -v "$p" 2>/dev/null || true)"
  [ -n "$c" ] || continue
  if [ "$c" = /usr/bin/python3 ] && [ "$(uname -s)" = Darwin ] && ! xcode-select -p >/dev/null 2>&1; then continue; fi
  if "$c" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  if [ "$(uname -s)" = Darwin ]; then
    echo "setup needs Python 3.9+. Install the Xcode Command Line Tools (also needed to build named apps):"
    echo "    xcode-select --install"
  else
    echo "setup needs Python 3.9+ (e.g. sudo apt install python3)"
  fi
  exit 1
fi
exec "$PY" "$HERE/setup/agent_setup.py" "$@"
