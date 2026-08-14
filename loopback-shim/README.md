# cptr IPv4 loopback shim

## Why this exists

cptr binds **IPv6-only** (`[::1]:38217`) because newt/Pangolin reach it that way.
But cptr's own **chrome-mode** browser viewer hardcodes `http://127.0.0.1:38217`
for its encoder page (`local_origin()` in `utils/browser/viewer.py`). IPv4
`127.0.0.1` is refused on an IPv6-only socket, so the chrome viewer shows
**"This site can't be reached — ERR_CONNECTION_REFUSED"** and chrome mode is
dead. (Proxy mode has no websockets, so neither engine could show a live app.)

## The fix

`shim.py` forwards `127.0.0.1:38217` (IPv4) -> `[::1]:38217` (IPv6) as raw TCP,
so HTTP and WebSocket both pass through. Verified: an IPv4 socket can co-bind the
same port as cptr's IPv6 socket (different address families, v6only=1).

Chosen over the alternatives because it is the only one that is BOTH durable and
non-invasive:
- **Not** patching `local_origin` -> `[::1]`: a `uv tool upgrade cptr` wipes it.
- **Not** rebinding cptr to `0.0.0.0` + changing the Pangolin target to
  `127.0.0.1`: correct, but risks the working `macbook.kingdomofluna.com` access
  and needs a dashboard change.
- This shim touches neither cptr nor Pangolin, and survives every cptr upgrade.

## Service

launchd `com.sandesh.cptr-shim` (RunAtLoad + KeepAlive).

    launchctl kickstart -k gui/$(id -u)/com.sandesh.cptr-shim   # restart
    curl http://127.0.0.1:38217/                                # should be 200

**Runs the venv python at `../desktop/.venv/bin/python`, not `/usr/bin/python3`.**
Xcode's python3 is blocked by macOS TCC from reading `~/Documents` under launchd
("Operation not permitted"); the venv python is already cleared for it.

The shim is stdlib-only. To also shim other IPv6-only local services, pass more
ports: `shim.py 38217 <other>`.
