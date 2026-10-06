#!/usr/bin/env bash
# mac-apps: run cptr, the cptr watchdog and Stuck Watch as properly named macOS apps, so privacy
# lists show "cptr" / "cptr Watchdog" / "Stuck Watch" instead of python3.x / bash, and cptr's Full
# Disk Access belongs to cptr.app alone. See README.md.
#
#   ./install.sh [--check]     report what's installed and what would change (changes nothing)
#   ./install.sh --cptr        wrap cptr (asks you to grant Full Disk Access first; needs you at the Mac)
#   ./install.sh --watchdog    wrap the cptr watchdog
#   ./install.sh --stuck-watch wrap Stuck Watch
#   ./install.sh --all         all three (cptr last: its restart can end the session you run this from)
#   ./install.sh --rollback    restore the LaunchAgents mac-apps changed
#   ./install.sh build "<Name>" [--bundle-id ID] -- <command...>   build any named app
#   ./install.sh probe "<Name>" <path...>                          open paths as that app
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

case "${1:-}" in -h|--help) sed -n 2,14p "$0"; exit 0 ;; esac
[ "$(uname -s)" = "Darwin" ] || { echo "mac-apps is macOS only"; exit 1; }

# The launcher is a ~60-line C program compiled on this Mac, and the installer uses the Command
# Line Tools' python3. Without them, /usr/bin/cc and /usr/bin/python3 are stubs that pop up an
# install dialog, so check first.
if ! xcode-select -p >/dev/null 2>&1 || ! xcrun --find cc >/dev/null 2>&1; then
  cat <<'MSG'
mac-apps needs the Xcode Command Line Tools (for `cc`, which compiles the small launcher inside
each app, and for python3). Install them, then re-run:

    xcode-select --install

(a dialog appears; click Install, about 5 minutes. Full Xcode works too.)
MSG
  exit 1
fi

exec /usr/bin/python3 "$HERE/mac_apps.py" "${@:---check}"
