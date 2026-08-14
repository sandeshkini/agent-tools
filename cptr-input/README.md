# cptr-input

Cross-platform input-injection daemon: drives the real mouse/keyboard of the
machine it runs on, from a unix socket. macOS and Linux, one protocol, one
client. Used by the browser-desktop app (`../desktop`) so a browser viewer can
control the host.

    client (any consumer)  --JSON over ~/.cptr/input.sock-->  cptr-input daemon
                                                                 |
                                        macOS: CGEvent   /   Linux: uinput
                                                                 v
                                                             the real OS

## Install

    ./install.sh          # detects macOS/Linux, installs backend + service

- **macOS**: builds & signs `macos/cptr-input.app`, installs launchd
  `com.sandesh.cptr-input`, then asks you to grant Accessibility once.
- **Linux**: installs `python-evdev`, grants `/dev/uinput` (input group + udev
  rule), installs a systemd --user service.

## Why two backends, one protocol

The wire protocol is **neutral** (see `protocol.md`): normalized coordinates and
W3C key `code`s, never OS-specific keycodes or absolute pixels. Each backend
translates locally, so the same `client.py` drives either machine unchanged.

- `macos/main.swift` — CGEvent. **Signed** with a stable bundle id, so the
  Accessibility grant survives `uv`/tool upgrades (an ad-hoc Python would lose it
  on every rebuild — that is the whole reason this is a native signed app).
- `linux/cptr_input.py` — python-evdev uinput devices (absolute pointer +
  keyboard). Permission is filesystem/group based; no code-signing/TCC concern.

Both: one thread/queue per connection with a 30s recv timeout, so a stuck client
cannot wedge the daemon. Every macOS event carries a `CPTR` userData tag so a
verifier tap can distinguish injected events from the human's.

## Files

    protocol.md            the neutral JSON op spec
    client.py              cptr-message -> neutral-op translator (both platforms)
    macos/main.swift       macOS backend (CGEvent)
    macos/build.sh         rebuild + sign
    macos/cptr-input.app   the signed bundle
    linux/cptr_input.py    Linux backend (uinput)
    install.sh             OS-detecting installer

## Permission status

`client.InputClient().ping()` returns `{"ok":true,"trusted":<bool>,"platform":...}`.

- macOS `trusted:false` -> Accessibility not granted. Grant it, then restart the
  daemon (TCC caches trust at process start):
  `launchctl kickstart -k gui/$(id -u)/com.sandesh.cptr-input`
- Linux `trusted:false` -> `/dev/uinput` not writable. Re-login after install so
  the `input` group applies, or check the udev rule.

## Gotchas

- macOS: a rebuild changes the cdhash and drops the grant. If two bundles with
  this id exist, TCC gets confused — keep exactly one and
  `tccutil reset Accessibility com.sandesh.cptr-input` before re-granting.
- Linux: `cmd` maps to Super. macOS `cmd` shortcuts won't be `cmd` on Linux;
  that's inherent to cross-platform modifier semantics.
- Linux Wayland: uinput works at the kernel level, so it drives Wayland too
  (unlike XTEST). Absolute positioning uses a 0..65535 virtual range the
  compositor maps to the screen.
