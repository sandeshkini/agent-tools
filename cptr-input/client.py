"""Client for the cptr-input daemon (macOS or Linux — same neutral protocol).

Translates cptr-style viewer input messages into neutral ops and sends them over
the unix socket. Sends NORMALIZED coordinates, so the daemon scales to its own
screen — no shared display config, works cross-platform unchanged.
"""

from __future__ import annotations

import json
import os
import socket

SOCKET_PATH = os.path.expanduser("~/.cptr/input.sock")

# CDP modifier bitmask (what cptr's client sends) -> neutral names.
_CDP_ALT, _CDP_CTRL, _CDP_META, _CDP_SHIFT = 1, 2, 4, 8


def _mods(bitmask: int) -> list[str]:
    out = []
    if bitmask & _CDP_META:
        out.append("cmd")
    if bitmask & _CDP_SHIFT:
        out.append("shift")
    if bitmask & _CDP_ALT:
        out.append("alt")
    if bitmask & _CDP_CTRL:
        out.append("ctrl")
    return out


_BUTTONS = {0: "left", 1: "middle", 2: "right"}


class InputClient:
    def __init__(self, path: str = SOCKET_PATH) -> None:
        self.path = path
        self._sock: socket.socket | None = None

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(self.path)
        self._sock = s

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> "InputClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def send(self, **cmd) -> dict:
        # Reconnect once on a dead socket (daemon restart, upgrade, re-grant).
        payload = json.dumps(cmd).encode() + b"\n"
        for attempt in (1, 2):
            try:
                if not self._sock:
                    self.connect()
                assert self._sock
                self._sock.sendall(payload)
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("daemon closed the connection")
                    buf += chunk
                return json.loads(buf or b"{}")
            except (OSError, ConnectionError):
                self.close()
                if attempt == 2:
                    raise
        return {}

    def ping(self) -> dict:
        return self.send(op="ping")

    def trusted(self) -> bool:
        return bool(self.ping().get("trusted"))

    # --- cptr message -> neutral op ---------------------------------------
    def dispatch(self, kind: str, data: dict) -> dict | None:
        """Translate one cptr viewer message and send it. Returns the reply,
        or None if the message maps to nothing."""
        if kind == "pointer":
            event = str(data.get("event", ""))
            nx, ny = _norm(data)
            if event == "move":
                return self.send(op="move", x=nx, y=ny)
            if event in ("down", "up"):
                button = str(data.get("button", "left")) or "left"
                if button == "none":
                    return None
                return self.send(op="button", button=button,
                                 action="down" if event == "down" else "up",
                                 clicks=max(1, int(data.get("click_count", 1))),
                                 x=nx, y=ny, mods=_mods(int(data.get("modifiers", 0))))
            return None

        if kind == "wheel":
            nx, ny = _norm(data)
            return self.send(op="scroll", dx=float(data.get("delta_x", 0)),
                             dy=float(data.get("delta_y", 0)), x=nx, y=ny,
                             mods=_mods(int(data.get("modifiers", 0))))

        if kind == "key":
            event = str(data.get("event", ""))
            text = str(data.get("text", ""))
            code = str(data.get("code", ""))
            modifiers = int(data.get("modifiers", 0))
            shortcut = bool(modifiers & (_CDP_META | _CDP_CTRL))
            # printable, no command modifier -> type as text (layout-independent)
            if event == "char" or (text and text.isprintable() and not shortcut):
                if event == "keyUp":
                    return None
                return self.send(op="text", text=text)
            if event not in ("keyDown", "keyUp") or not code:
                return None
            return self.send(op="key", code=code,
                             action="down" if event == "keyDown" else "up",
                             mods=_mods(modifiers))

        if kind in ("paste", "text"):
            return self.send(op="text", text=str(data.get("text", "")))

        return None


def _norm(data: dict) -> tuple[float, float]:
    """Extract normalized 0..1 coords from a cptr message."""
    x, y = float(data.get("x", 0)), float(data.get("y", 0))
    if data.get("normalized"):
        return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
    # absolute frame coords: scale by frame size if provided, else assume already 0..1
    fw = float(data.get("frame_width", 0)) or 1.0
    fh = float(data.get("frame_height", 0)) or 1.0
    return max(0.0, min(1.0, x / fw)), max(0.0, min(1.0, y / fh))
