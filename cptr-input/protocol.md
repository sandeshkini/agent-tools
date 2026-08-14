# cptr-input protocol

A tiny input-injection daemon. One per machine. Speaks newline-delimited JSON
over a unix socket at `~/.cptr/input.sock` (mode 0600). The same protocol is
implemented by a macOS backend (CGEvent) and a Linux backend (uinput), so the
same client drives either.

**Neutral by design.** Earlier the wire carried macOS specifics (CGEventType
ints, kVK keycodes, absolute points). That could never port. Now every op is
OS-neutral and each backend translates locally. Coordinates are **normalized
0..1**, so each machine scales to its own screen — no shared display config.

## Ops (one JSON object per line; each gets one JSON reply)

    {"op":"ping"}
      -> {"ok":true,"trusted":<bool>,"platform":"macos"|"linux"}
      trusted: macOS = Accessibility granted; Linux = /dev/uinput writable.

    {"op":"move","x":0.5,"y":0.5}
      Move the pointer to a normalized screen position.

    {"op":"button","button":"left"|"right"|"middle",
     "action":"down"|"up","clicks":1,"x":0.5,"y":0.5}
      Press/release a mouse button. x,y optional (move there first if present).
      clicks: 1 single, 2 double (macOS uses it; Linux emits N click pairs).

    {"op":"scroll","dx":0,"dy":120,"x":0.5,"y":0.5}
      Wheel. Positive dy = content scrolls up (DOM convention); each backend
      applies its own sign. x,y optional.

    {"op":"key","code":"KeyA","action":"down"|"up",
     "mods":["cmd","shift","alt","ctrl"]}
      Key by W3C `code` (KeyA, Digit1, Enter, ArrowUp, F5, ...). Modifiers by
      name; each backend maps them (cmd -> ⌘ on macOS, Super on Linux).

    {"op":"text","text":"hello 🚀"}
      Type a literal string, layout-independent. macOS: CGEventKeyboardSetUnicodeString.
      Linux: resolve each char to KEY_* + shift, or a temporary keymap.

## Backends

- macOS: `macos/cptr-input.app` (signed Swift). Signature is stable, so the
  Accessibility grant survives `uv`/tool upgrades. launchd `com.sandesh.cptr-input`.
- Linux: `linux/cptr_input.py` (python-evdev uinput device). Needs `/dev/uinput`
  access (input group + udev rule, set by the installer). systemd user service
  `cptr-input`.

## Install

`./install.sh` detects the OS, builds/installs the right backend, wires the
service, and prints how to grant permission.
