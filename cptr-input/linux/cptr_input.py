#!/usr/bin/env python3
"""cptr-input (Linux backend).

Neutral JSON protocol (see ../protocol.md) -> uinput virtual devices. One
connection per client, each on its own thread with a recv timeout, mirroring the
macOS backend so a stuck peer cannot wedge the daemon.

Needs write access to /dev/uinput (the installer adds a udev rule + input group).
No code-signing/TCC concern on Linux — permission is filesystem/group based.

Depends only on python-evdev (`pip install evdev`).
"""

from __future__ import annotations

import json
import os
import socket
import threading

from evdev import UInput, ecodes as e

SOCKET_PATH = os.path.expanduser("~/.cptr/input.sock")

# Absolute pointer range. The compositor maps 0..ABS_MAX onto the whole screen,
# so we never need the real pixel size -- normalized coords scale cleanly.
ABS_MAX = 65535

# W3C `code` -> Linux KEY_*. Same identifiers the macOS backend uses, so the
# client is identical across platforms. Printable text uses the "text" op.
KEYMAP = {
    "KeyA": e.KEY_A, "KeyB": e.KEY_B, "KeyC": e.KEY_C, "KeyD": e.KEY_D,
    "KeyE": e.KEY_E, "KeyF": e.KEY_F, "KeyG": e.KEY_G, "KeyH": e.KEY_H,
    "KeyI": e.KEY_I, "KeyJ": e.KEY_J, "KeyK": e.KEY_K, "KeyL": e.KEY_L,
    "KeyM": e.KEY_M, "KeyN": e.KEY_N, "KeyO": e.KEY_O, "KeyP": e.KEY_P,
    "KeyQ": e.KEY_Q, "KeyR": e.KEY_R, "KeyS": e.KEY_S, "KeyT": e.KEY_T,
    "KeyU": e.KEY_U, "KeyV": e.KEY_V, "KeyW": e.KEY_W, "KeyX": e.KEY_X,
    "KeyY": e.KEY_Y, "KeyZ": e.KEY_Z,
    "Digit1": e.KEY_1, "Digit2": e.KEY_2, "Digit3": e.KEY_3, "Digit4": e.KEY_4,
    "Digit5": e.KEY_5, "Digit6": e.KEY_6, "Digit7": e.KEY_7, "Digit8": e.KEY_8,
    "Digit9": e.KEY_9, "Digit0": e.KEY_0,
    "Equal": e.KEY_EQUAL, "Minus": e.KEY_MINUS, "BracketRight": e.KEY_RIGHTBRACE,
    "BracketLeft": e.KEY_LEFTBRACE, "Quote": e.KEY_APOSTROPHE,
    "Semicolon": e.KEY_SEMICOLON, "Backslash": e.KEY_BACKSLASH,
    "Comma": e.KEY_COMMA, "Slash": e.KEY_SLASH, "Period": e.KEY_DOT,
    "Backquote": e.KEY_GRAVE,
    "Enter": e.KEY_ENTER, "Tab": e.KEY_TAB, "Space": e.KEY_SPACE,
    "Backspace": e.KEY_BACKSPACE, "Escape": e.KEY_ESC, "Delete": e.KEY_DELETE,
    "Home": e.KEY_HOME, "End": e.KEY_END, "PageUp": e.KEY_PAGEUP,
    "PageDown": e.KEY_PAGEDOWN,
    "ArrowLeft": e.KEY_LEFT, "ArrowRight": e.KEY_RIGHT,
    "ArrowUp": e.KEY_UP, "ArrowDown": e.KEY_DOWN,
    "MetaLeft": e.KEY_LEFTMETA, "ShiftLeft": e.KEY_LEFTSHIFT,
    "CapsLock": e.KEY_CAPSLOCK, "AltLeft": e.KEY_LEFTALT,
    "ControlLeft": e.KEY_LEFTCTRL, "ShiftRight": e.KEY_RIGHTSHIFT,
    "AltRight": e.KEY_RIGHTALT, "ControlRight": e.KEY_RIGHTCTRL,
    "F1": e.KEY_F1, "F2": e.KEY_F2, "F3": e.KEY_F3, "F4": e.KEY_F4,
    "F5": e.KEY_F5, "F6": e.KEY_F6, "F7": e.KEY_F7, "F8": e.KEY_F8,
    "F9": e.KEY_F9, "F10": e.KEY_F10, "F11": e.KEY_F11, "F12": e.KEY_F12,
}

# cmd -> Super on Linux (closest literal to the macOS Command key).
MODMAP = {
    "cmd": e.KEY_LEFTMETA, "meta": e.KEY_LEFTMETA, "super": e.KEY_LEFTMETA,
    "shift": e.KEY_LEFTSHIFT, "alt": e.KEY_LEFTALT, "option": e.KEY_LEFTALT,
    "ctrl": e.KEY_LEFTCTRL, "control": e.KEY_LEFTCTRL,
}

# US-layout map for the "text" op: char -> (KEY_*, needs_shift).
_UNSHIFTED = "abcdefghijklmnopqrstuvwxyz0123456789 \n\t-=[]\\;',./`"
_SHIFTED = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ)!@#$%^&*(   _+{}|:"<>?~'


def _char_key(ch: str):
    base = {
        " ": (e.KEY_SPACE, False), "\n": (e.KEY_ENTER, False), "\t": (e.KEY_TAB, False),
    }
    if ch in base:
        return base[ch]
    lower = ch.lower()
    name = f"Key{lower.upper()}" if lower.isalpha() else None
    if name and name in KEYMAP:
        return KEYMAP[name], ch.isupper()
    digits = {str(d): getattr(e, f"KEY_{d}") for d in range(10)}
    if ch in digits:
        return digits[ch], False
    punct = {
        "-": (e.KEY_MINUS, False), "=": (e.KEY_EQUAL, False), "[": (e.KEY_LEFTBRACE, False),
        "]": (e.KEY_RIGHTBRACE, False), "\\": (e.KEY_BACKSLASH, False), ";": (e.KEY_SEMICOLON, False),
        "'": (e.KEY_APOSTROPHE, False), ",": (e.KEY_COMMA, False), ".": (e.KEY_DOT, False),
        "/": (e.KEY_SLASH, False), "`": (e.KEY_GRAVE, False),
        "_": (e.KEY_MINUS, True), "+": (e.KEY_EQUAL, True), ":": (e.KEY_SEMICOLON, True),
        '"': (e.KEY_APOSTROPHE, True), "<": (e.KEY_COMMA, True), ">": (e.KEY_DOT, True),
        "?": (e.KEY_SLASH, True), "~": (e.KEY_GRAVE, True), "!": (e.KEY_1, True),
        "@": (e.KEY_2, True), "#": (e.KEY_3, True), "$": (e.KEY_4, True), "%": (e.KEY_5, True),
        "^": (e.KEY_6, True), "&": (e.KEY_7, True), "*": (e.KEY_8, True), "(": (e.KEY_9, True),
        ")": (e.KEY_0, True), "{": (e.KEY_LEFTBRACE, True), "}": (e.KEY_RIGHTBRACE, True),
        "|": (e.KEY_BACKSLASH, True),
    }
    return punct.get(ch)


class Injector:
    """Owns two uinput devices: absolute pointer + keyboard."""

    def __init__(self) -> None:
        # absolute pointer
        abs_caps = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
            e.EV_ABS: [
                (e.ABS_X, (0, 0, ABS_MAX, 0, 0, 0)),
                (e.ABS_Y, (0, 0, ABS_MAX, 0, 0, 0)),
            ],
            e.EV_REL: [e.REL_WHEEL, e.REL_HWHEEL],
        }
        self.pointer = UInput(abs_caps, name="cptr-input-pointer")
        # keyboard: every key we might emit
        keys = sorted(set(KEYMAP.values()) | set(MODMAP.values()))
        self.keyboard = UInput({e.EV_KEY: keys}, name="cptr-input-keyboard")
        self.held = 0

    def move(self, nx: float, ny: float) -> None:
        self.pointer.write(e.EV_ABS, e.ABS_X, int(max(0, min(1, nx)) * ABS_MAX))
        self.pointer.write(e.EV_ABS, e.ABS_Y, int(max(0, min(1, ny)) * ABS_MAX))
        self.pointer.syn()

    def button(self, name: str, down: bool, clicks: int, x, y) -> None:
        if x is not None and y is not None:
            self.move(x, y)
        btn = {"right": e.BTN_RIGHT, "middle": e.BTN_MIDDLE}.get(name, e.BTN_LEFT)
        reps = clicks if (down and clicks > 1) else 1
        for _ in range(reps):
            self.pointer.write(e.EV_KEY, btn, 1 if down else 0)
            self.pointer.syn()

    def scroll(self, dx: float, dy: float, x, y) -> None:
        if x is not None and y is not None:
            self.move(x, y)
        # DOM positive dy = scroll down; Linux REL_WHEEL positive = up
        if dy:
            self.pointer.write(e.EV_REL, e.REL_WHEEL, -1 if dy > 0 else 1)
        if dx:
            self.pointer.write(e.EV_REL, e.REL_HWHEEL, 1 if dx > 0 else -1)
        self.pointer.syn()

    def key(self, code: str, down: bool, mods: list[str]) -> bool:
        kc = KEYMAP.get(code)
        if kc is None:
            return False
        mod_codes = [MODMAP[m] for m in mods if m in MODMAP]
        if down:
            for mc in mod_codes:
                self.keyboard.write(e.EV_KEY, mc, 1)
        self.keyboard.write(e.EV_KEY, kc, 1 if down else 0)
        if not down:
            for mc in mod_codes:
                self.keyboard.write(e.EV_KEY, mc, 0)
        self.keyboard.syn()
        return True

    def text(self, s: str) -> None:
        for ch in s:
            mapped = _char_key(ch)
            if not mapped:
                continue
            kc, shift = mapped
            if shift:
                self.keyboard.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
            self.keyboard.write(e.EV_KEY, kc, 1)
            self.keyboard.write(e.EV_KEY, kc, 0)
            if shift:
                self.keyboard.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
            self.keyboard.syn()


def uinput_writable() -> bool:
    return os.access("/dev/uinput", os.W_OK)


def make_handler(inj: Injector | None):
    def handle(cmd: dict) -> dict:
        op = cmd.get("op", "")
        if op in ("ping", "trusted"):
            return {"ok": True, "trusted": uinput_writable(), "platform": "linux"}
        if inj is None:
            return {"ok": False, "error": "/dev/uinput not writable"}
        try:
            if op == "move":
                inj.move(cmd.get("x", 0), cmd.get("y", 0))
            elif op == "button":
                inj.button(cmd.get("button", "left"),
                           cmd.get("action", "down") == "down",
                           int(cmd.get("clicks", 1)), cmd.get("x"), cmd.get("y"))
            elif op == "scroll":
                inj.scroll(cmd.get("dx", 0), cmd.get("dy", 0), cmd.get("x"), cmd.get("y"))
            elif op == "key":
                if not inj.key(cmd.get("code", ""), cmd.get("action", "down") == "down",
                               cmd.get("mods", [])):
                    return {"ok": False, "error": f"unmapped key {cmd.get('code')}"}
            elif op == "text":
                inj.text(cmd.get("text", ""))
            else:
                return {"ok": False, "error": f"unknown op {op}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}
    return handle


def serve_client(conn: socket.socket, handle) -> None:
    conn.settimeout(30)
    buf = b""
    try:
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line:
                    continue
                try:
                    reply = handle(json.loads(line))
                except Exception:
                    reply = {"ok": False, "error": "bad json"}
                conn.sendall(json.dumps(reply).encode() + b"\n")
    except (OSError, socket.timeout):
        pass
    finally:
        conn.close()


def main() -> None:
    os.makedirs(os.path.dirname(SOCKET_PATH), exist_ok=True)
    inj = Injector() if uinput_writable() else None
    handle = make_handler(inj)

    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o600)
    srv.listen(16)
    print(f"cptr-input (linux) on {SOCKET_PATH} trusted={uinput_writable()}", flush=True)

    while True:
        conn, _ = srv.accept()
        threading.Thread(target=serve_client, args=(conn, handle), daemon=True).start()


if __name__ == "__main__":
    main()
