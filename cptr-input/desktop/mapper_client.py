"""Talk to the cptr-input helper over its unix socket.

Python keeps all the mapping logic (mapper.py); the helper just posts what it is
told. This is the piece cptr's backend would call instead of CDP.
"""

from __future__ import annotations

import json
import os
import socket

import mapper

SOCKET_PATH = os.path.expanduser("~/.cptr/input.sock")

_FLAG_BITS = {
    "cmd": mapper.FLAG_COMMAND, "shift": mapper.FLAG_SHIFT,
    "alt": mapper.FLAG_ALTERNATE, "ctrl": mapper.FLAG_CONTROL,
}


def _bits(desc: str) -> int:
    if not desc or desc == "none":
        return 0
    return sum(_FLAG_BITS.get(n, 0) for n in desc.split("+"))


class InputClient:
    def __init__(self, path: str = SOCKET_PATH) -> None:
        self.path = path
        self._sock: socket.socket | None = None

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)          # never block the caller forever
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
        # Reconnect once on a dead socket. The helper may restart (upgrades, a
        # crash, a re-grant) and this persistent connection would otherwise stay
        # broken forever -- exactly the "clicks silently stopped" failure.
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
                        raise ConnectionError("helper closed the connection")
                    buf += chunk
                return json.loads(buf or b"{}")
            except (OSError, ConnectionError):
                self.close()          # drop the dead socket, retry a fresh one
                if attempt == 2:
                    raise
        return {}

    # --- convenience ------------------------------------------------------
    def ping(self) -> dict:
        return self.send(op="ping")

    def trusted(self) -> bool:
        return bool(self.ping().get("trusted"))

    def execute(self, event: mapper.PlannedEvent) -> dict:
        d = event.detail
        flags = _bits(d.get("flags", ""))
        if event.kind == "mouse":
            return self.send(op="mouse", type=d["type"], x=d["x"], y=d["y"],
                             clicks=d.get("clicks", 0), flags=flags)
        if event.kind == "scroll":
            return self.send(op="scroll", dy=d["dy"], dx=d["dx"], flags=flags)
        if event.kind == "key":
            return self.send(op="key", keycode=d["keycode"], down=d["down"], flags=flags)
        if event.kind == "unicode":
            return self.send(op="unicode", text=d["text"])
        raise KeyError(f"cannot execute {event.kind}")

    def dispatch(self, display: mapper.Display, kind: str, data: dict,
                 buttons_held: int = 0) -> list[dict]:
        """The whole path: cptr client message -> planned events -> posted."""
        return [self.execute(ev)
                for ev in mapper.plan(display, kind, data, buttons_held)]
