"""Standalone browser desktop: stream this Mac's screen to a browser tab and
control it from there.

    encoder Chrome  --H.264-->  this server  --H.264-->  your browser
                                     ^                        |
                                     +----- input events <-----+
                                     |
                              cptr-input helper (CGEvent)

Deliberately standalone: it survives cptr upgrades and does not fight cptr's
internals. Folding it into cptr later means reusing this server logic behind a
cptr route -- the client protocol is already the one cptr's frontend speaks.

Run:  .venv/bin/python desktop_server.py
Then: http://127.0.0.1:38218/
"""

from __future__ import annotations

import asyncio
import atexit
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

import sys as _sys, os as _os
# the cptr-input client is a sibling tool in this repo; resolve it relative to here
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "cptr-input"))
import client  # neutral cptr-input client (macOS/Linux)
import mapper
import probe_display
import threading

_input_lock = threading.Lock()

PORT = 38218
STATIC = Path(__file__).parent / "static"

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]


class Hub:
    """Fans one encoder stream out to many viewers."""

    def __init__(self) -> None:
        self.viewers: set[WebSocket] = set()
        self.encoder: WebSocket | None = None
        self.last_keyframe: bytes | None = None
        self.codec: str | None = None

    async def broadcast(self, packet: bytes) -> None:
        if packet and packet[0] == 1:
            self.last_keyframe = packet
        dead = []
        for ws in self.viewers:
            try:
                await ws.send_bytes(packet)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.viewers.discard(ws)

    async def broadcast_text(self, text: str) -> None:
        for ws in list(self.viewers):
            try:
                await ws.send_text(text)
            except Exception:
                self.viewers.discard(ws)

    async def request_keyframe(self) -> None:
        """A joining viewer cannot decode mid-GOP -- ask for a fresh keyframe."""
        if self.encoder:
            try:
                await self.encoder.send_text("keyframe")
            except Exception:
                pass


hub = Hub()
input_client = client.InputClient()
_chrome: subprocess.Popen | None = None
_profile: str | None = None


def display() -> mapper.Display:
    # Probe LIVE every call, never cache. The user can change resolution or swap
    # monitors while the service runs; a cached size sends clicks to the wrong
    # place and is why input drifted after a display change.
    d = [x for x in probe_display.displays() if x["main"]][0]
    return mapper.Display(d["origin"][0], d["origin"][1], *d["points"])


def start_encoder_chrome() -> None:
    global _chrome, _profile
    browser = next((p for p in CHROME_PATHS if Path(p).exists()), None)
    if not browser:
        print("  no Chrome found -- encoder will not start")
        return
    _profile = tempfile.mkdtemp(prefix="agent-desktop-")
    # Direct Popen of the Chrome binary -- this is what streams reliably. The
    # regex value auto-selects the whole screen so no picker dialog appears.
    _chrome = subprocess.Popen([
        browser,
        f"--user-data-dir={_profile}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-background-networking",
        "--auto-select-desktop-capture-source=Entire screen",
        "--window-position=-32000,-32000",       # park encoder window offscreen
        f"http://127.0.0.1:{PORT}/encoder",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  encoder chrome started (pid {_chrome.pid}, profile {_profile})")


def stop_encoder_chrome() -> None:
    if _chrome and _chrome.poll() is None:
        _chrome.terminate()
        try:
            _chrome.wait(timeout=5)
        except Exception:
            _chrome.kill()
    if _profile:
        subprocess.run(["pkill", "-f", _profile], capture_output=True)
    if _profile:
        shutil.rmtree(_profile, ignore_errors=True)


atexit.register(stop_encoder_chrome)


@asynccontextmanager
async def lifespan(app: FastAPI):
    d = display()
    print(f"  display : {d.width:g}x{d.height:g} pts")
    try:
        trusted = input_client.trusted()
    except Exception as exc:
        trusted = False
        print(f"  helper  : UNREACHABLE ({exc}) -- is com.sandesh.cptr-input running?")
    else:
        print(f"  helper  : {'trusted' if trusted else 'NOT TRUSTED -- clicks will vanish'}")
    start_encoder_chrome()
    print(f"  open    : http://127.0.0.1:{PORT}/")
    yield
    stop_encoder_chrome()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "viewer.html")


@app.get("/encoder")
async def encoder_page() -> FileResponse:
    return FileResponse(STATIC / "encoder.html")


@app.post("/clientlog")
async def clientlog(payload: dict) -> dict:
    print(f"  [client] {payload.get('msg')}", flush=True)
    return {"ok": True}


@app.get("/status")
async def status() -> dict:
    d = display()
    try:
        trusted = input_client.trusted()
    except Exception:
        trusted = None
    return {
        "viewers": len(hub.viewers),
        "encoder_connected": hub.encoder is not None,
        "have_keyframe": hub.last_keyframe is not None,
        "codec": hub.codec,
        "helper_trusted": trusted,
        "display": {"width": d.width, "height": d.height},
    }


@app.websocket("/ws/encoder")
async def ws_encoder(ws: WebSocket) -> None:
    await ws.accept()
    hub.encoder = ws
    print("  encoder connected")
    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if (text := message.get("text")) is not None:
                # codec announcement -- viewers cannot decode without it
                hub.codec = text
                print(f"  codec: {text}")
                await hub.broadcast_text(text)
                continue
            packet = message.get("bytes")
            if packet:
                await hub.broadcast(packet)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"  encoder error: {exc}")
    finally:
        hub.encoder = None
        print("  encoder disconnected")


@app.websocket("/ws/viewer")
async def ws_viewer(ws: WebSocket) -> None:
    await ws.accept()
    hub.viewers.add(ws)
    print(f"  viewer connected ({len(hub.viewers)} total)")
    if hub.codec:
        await ws.send_text(hub.codec)   # decoder config must arrive first
    await hub.request_keyframe()
    if hub.last_keyframe:
        await ws.send_bytes(hub.last_keyframe)

    loop = asyncio.get_running_loop()
    try:
        while True:
            data = await ws.receive_json()
            kind = str(data.pop("kind", ""))
            data.pop("buttons_held", None)
            if kind not in {"pointer", "wheel", "key", "paste", "text"}:
                continue
            # Sub-millisecond; a lock serializes access to the one daemon
            # socket so concurrent viewers cannot corrupt each other's I/O.
            with _input_lock:
                _dispatch(kind, data)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"  viewer error: {exc}")
    finally:
        hub.viewers.discard(ws)
        print(f"  viewer disconnected ({len(hub.viewers)} left)")


def _dispatch(kind: str, data: dict) -> None:
    try:
        input_client.dispatch(kind, data)
    except Exception as exc:
        print(f"  input dispatch failed: {exc}")


if __name__ == "__main__":
    import uvicorn

    # 0.0.0.0, not "::" -- on macOS a "::" bind goes IPv6-ONLY and kills IPv4/LAN
    # access. So point the Pangolin resource at 127.0.0.1:38218 explicitly;
    # a "localhost" target makes newt dial [::1] and 502.
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
