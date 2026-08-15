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

--- why the fan-out is queued, and not a plain `await ws.send_bytes()` ---------
uvicorn's websocket protocol pauses transport reading after every message it
hands the app, and only resumes when the app comes back around to `receive()`
(uvicorn/protocols/websockets/websockets_sansio_impl.py: `send_receive_event_to_app`
-> `transport.pause_reading()`). It also runs a keepalive ping every 20s and
kills the connection with 1011 if no pong is *read* within ws_ping_timeout.

Those two facts make any blocking fan-out fatal. If the encoder's receive loop
awaits a send to a slow viewer -- a real one over the WAN tunnel, whose transport
write buffer passes the high-water mark, so `asgi_send` parks on
`await self.writable.wait()` -- then the ENCODER connection stays read-paused.
Chrome answers the keepalive ping instantly, the server never reads the pong, and
20s later uvicorn tears down a perfectly healthy encoder connection. Symptom:
"encoder disconnected" moments after a remote viewer joins, encoder Chrome still
alive, stream frozen. It never reproduces with a localhost viewer, because
loopback drains faster than the encoder fills.

So: viewers each get a bounded queue and their own writer task. A slow viewer
drops frames and resyncs on the next keyframe. It can never stall the encoder.
"""

from __future__ import annotations

import asyncio
import atexit
import glob
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

import sys as _sys, os as _os
# the cptr-input client lives one directory up (this is now cptr-input/desktop/)
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))
import client  # neutral cptr-input client (macOS/Linux)
import mapper
import probe_display

# Overridable only so a second instance can be brought up on a scratch port to
# test changes without taking the live service down.
PORT = int(os.environ.get("AGENT_DESKTOP_PORT", "38218"))
STATIC = Path(__file__).parent / "static"

# One viewer's backlog. ~8 frames at 30fps is a quarter second of slack: enough
# to ride out jitter, small enough that the transport buffer stays well under the
# high-water mark so keepalive pings are never stuck behind video.
VIEWER_QUEUE = 8
# Slowest we will ask the encoder for a resync keyframe. A viewer that is
# permanently too slow drops nearly every frame, and a keyframe per drop would
# aim a burst of extra bitrate at the link that is already the bottleneck.
KEYFRAME_MIN_GAP = 2.0
# A single send that cannot complete in this long is not a slow viewer, it is a
# dead one (closed lid, dropped LTE). asyncio will not finish closing a transport
# whose write buffer never drains, so without this the entry lingers forever and
# /status over-reports viewers.
VIEWER_SEND_TIMEOUT = 30.0
# Restart the encoder browser if nothing has connected back in this long. The
# page reconnects itself in ~1s, so reaching this means the tab is truly gone.
ENCODER_RESTART_AFTER = 45.0
# Encoder connected but silent this long (it heartbeats every 5s) -> also restart.
ENCODER_STALL_AFTER = 60.0
SUPERVISOR_TICK = 5.0

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]

# Port-scoped, so reaping orphans from a previous run of THIS instance can never
# kill a second instance brought up on a scratch port to test a change.
TEMP_PROFILE_PREFIX = f"agent-desktop-{PORT}-"


# --- logging ----------------------------------------------------------------
# stdout is a launchd-redirected file, so print() is BLOCK buffered: lines sat in
# an 8KB buffer for minutes, interleaved wrongly with uvicorn's (line-buffered)
# stderr, and were lost outright when `launchctl kickstart -k` SIGKILLed us. That
# is most of why the original failure was so hard to read. Timestamp and flush.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:  # pragma: no cover - very old interpreters
    pass


def log(msg: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}  {msg}", flush=True)


def _why(exc: BaseException) -> str:
    """A disconnect reason you can actually act on, instead of a bare string."""
    if isinstance(exc, WebSocketDisconnect):
        reason = getattr(exc, "reason", "") or ""
        hint = ""
        if exc.code == 1011 and "keepalive" in reason.lower():
            hint = "  <- server-side keepalive ping timeout: the handler stalled"
        elif exc.code == 1006:
            hint = "  <- abnormal close, no close frame (process died / network cut)"
        return f"close code={exc.code} reason={reason!r}{hint}"
    if isinstance(exc, asyncio.TimeoutError):
        return (f"send stalled >{VIEWER_SEND_TIMEOUT:.0f}s"
                "  <- peer stopped reading entirely (dead link)")
    return f"{type(exc).__name__}: {exc}"


class Viewer:
    """One connected browser, with its own bounded backlog and writer task.

    The queue is the whole point: `Hub.broadcast` must never await this socket.
    """

    __slots__ = ("ws", "queue", "dropped", "sent", "task", "joined")

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.queue: asyncio.Queue[bytes | str | None] = asyncio.Queue(VIEWER_QUEUE)
        self.dropped = 0
        self.sent = 0
        self.task: asyncio.Task | None = None
        self.joined = time.monotonic()

    def offer(self, item: bytes | str) -> bool:
        """Non-blocking. Returns False if the viewer is too slow and we dropped."""
        try:
            self.queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            # This viewer cannot keep up. Throw the whole backlog away rather than
            # trickling stale frames: it will resync on the keyframe we ask for.
            while True:
                try:
                    self.queue.get_nowait()
                    self.dropped += 1
                except asyncio.QueueEmpty:
                    break
            try:
                self.queue.put_nowait(item)
            except asyncio.QueueFull:  # pragma: no cover
                self.dropped += 1
            return False

    async def pump(self) -> None:
        """Owns every write to this socket. Blocking here harms nobody else."""
        while True:
            item = await self.queue.get()
            if item is None:
                return
            send = (self.ws.send_text(item) if isinstance(item, str)
                    else self.ws.send_bytes(item))
            # Cancelling here is safe: asgi_send parks on `await writable.wait()`
            # before it has written anything, so nothing is half-sent.
            await asyncio.wait_for(send, VIEWER_SEND_TIMEOUT)
            self.sent += 1


class Hub:
    """Fans one encoder stream out to many viewers, without head-of-line blocking."""

    def __init__(self) -> None:
        self.viewers: set[Viewer] = set()
        self.encoder: WebSocket | None = None
        self.last_keyframe: bytes | None = None
        self.codec: str | None = None
        # diagnostics -- so /status answers "what broke" without a log dive
        self.encoder_since: float | None = None
        self.encoder_gone_since: float = time.monotonic()
        self.encoder_last_msg: float = 0.0
        self.last_disconnect: str | None = None
        self.encoder_connects = 0
        self.packets = 0
        self.restarts = 0
        self.want_keyframe = False
        self.last_keyframe_ask = 0.0

    def broadcast(self, packet: bytes) -> None:
        """Synchronous by design: the encoder loop must never await a viewer."""
        if packet and packet[0] == 1:
            self.last_keyframe = packet
        self.packets += 1
        for v in list(self.viewers):
            if not v.offer(packet):
                # it dropped its backlog; it needs a keyframe to decode again
                self.want_keyframe = True

    def due_for_keyframe(self) -> bool:
        """Rate limit. A viewer that is permanently too slow drops every frame, and
        one keyframe request per dropped frame would be a keyframe storm -- more
        bitrate aimed at the link that is already the bottleneck."""
        if not self.want_keyframe:
            return False
        now = time.monotonic()
        if now - self.last_keyframe_ask < KEYFRAME_MIN_GAP:
            return False
        self.want_keyframe = False
        self.last_keyframe_ask = now
        return True

    def broadcast_text(self, text: str) -> None:
        for v in list(self.viewers):
            v.offer(text)

    async def request_keyframe(self) -> None:
        """A joining viewer cannot decode mid-GOP -- ask for a fresh keyframe."""
        ws = self.encoder
        if ws is None:
            return
        try:
            await ws.send_text("keyframe")
        except Exception as exc:
            log(f"  keyframe request failed: {_why(exc)}")


hub = Hub()
input_client = client.InputClient()
_input_lock = threading.Lock()
_chrome: subprocess.Popen | None = None
_profile: str | None = None
_chrome_lock = threading.Lock()


# --- input: off the event loop ----------------------------------------------
# input_client.dispatch() is a BLOCKING unix-socket round-trip with a 5s timeout.
# Running it inline in the viewer coroutine stalled the whole event loop -- which,
# per the module docstring, is exactly the thing that gets connections killed. One
# worker thread keeps global event ordering while the loop stays free.
_input_q: queue.Queue = queue.Queue(maxsize=512)
_input_dropped = 0


def _input_worker() -> None:
    while True:
        item = _input_q.get()
        if item is None:
            return
        kind, data = item
        try:
            with _input_lock:
                input_client.dispatch(kind, data)
        except Exception as exc:
            log(f"  input dispatch failed: {type(exc).__name__}: {exc}")


threading.Thread(target=_input_worker, daemon=True, name="input").start()


def _enqueue_input(kind: str, data: dict) -> None:
    global _input_dropped
    try:
        _input_q.put_nowait((kind, data))
    except queue.Full:
        # Shed the oldest instead of the newest: at this depth the stale entry is
        # a mouse move, and dropping a button-up would strand a held button.
        try:
            _input_q.get_nowait()
        except queue.Empty:
            pass
        try:
            _input_q.put_nowait((kind, data))
        except queue.Full:
            _input_dropped += 1


def display() -> mapper.Display:
    # Probe LIVE every call, never cache. The user can change resolution or swap
    # monitors while the service runs; a cached size sends clicks to the wrong
    # place and is why input drifted after a display change.
    d = [x for x in probe_display.displays() if x["main"]][0]
    return mapper.Display(d["origin"][0], d["origin"][1], *d["points"])


def _trusted() -> bool | None:
    try:
        with _input_lock:
            return input_client.trusted()
    except Exception:
        return None


# --- encoder browser --------------------------------------------------------
def _reap_stale_profiles() -> None:
    """`launchctl kickstart -k` SIGKILLs us, so atexit never runs and the previous
    encoder Chrome is orphaned with its temp profile. Clean those up on boot."""
    for path in glob.glob(os.path.join(tempfile.gettempdir(), TEMP_PROFILE_PREFIX + "*")):
        if _profile and os.path.abspath(path) == os.path.abspath(_profile):
            continue
        subprocess.run(["pkill", "-f", path], capture_output=True)
        shutil.rmtree(path, ignore_errors=True)
        log(f"  reaped orphaned encoder profile {path}")


def start_encoder_chrome() -> None:
    global _chrome, _profile
    with _chrome_lock:
        browser = next((p for p in CHROME_PATHS if Path(p).exists()), None)
        if not browser:
            log("  no Chrome found -- encoder will not start")
            return
        _profile = tempfile.mkdtemp(prefix=TEMP_PROFILE_PREFIX)
        # Direct Popen of the Chrome binary -- this is what streams reliably. The
        # regex value auto-selects the whole screen so no picker dialog appears.
        _chrome = subprocess.Popen([
            browser,
            f"--user-data-dir={_profile}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking",
            "--auto-select-desktop-capture-source=Entire screen",
            "--window-position=-32000,-32000",       # park encoder window offscreen
            # Parked offscreen, macOS reports the window occluded, so Chrome
            # backgrounds the renderer: capture frames slow to a crawl and the
            # page's reconnect setTimeout gets throttled toward once a minute.
            # These three keep the encoder tab running at foreground priority.
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
            f"http://127.0.0.1:{PORT}/encoder",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log(f"  encoder chrome started (pid {_chrome.pid}, profile {_profile})")


def stop_encoder_chrome() -> None:
    global _chrome, _profile
    with _chrome_lock:
        if _chrome and _chrome.poll() is None:
            _chrome.terminate()
            try:
                _chrome.wait(timeout=5)
            except Exception:
                _chrome.kill()
        _chrome = None
        if _profile:
            # kills the helper processes too -- they do not die with the parent
            subprocess.run(["pkill", "-f", _profile], capture_output=True)
            shutil.rmtree(_profile, ignore_errors=True)
            _profile = None


def restart_encoder_chrome(why: str) -> None:
    hub.restarts += 1
    log(f"  watchdog: restarting encoder chrome (#{hub.restarts}) -- {why}")
    stop_encoder_chrome()
    start_encoder_chrome()


atexit.register(stop_encoder_chrome)


async def _supervisor() -> None:
    """Self-heal, so nobody has to notice and run `launchctl kickstart -k`."""
    while True:
        await asyncio.sleep(SUPERVISOR_TICK)
        try:
            now = time.monotonic()
            if hub.encoder is None:
                gone = now - hub.encoder_gone_since
                if gone > ENCODER_RESTART_AFTER:
                    hub.encoder_gone_since = now  # re-arm before the slow restart
                    await asyncio.to_thread(
                        restart_encoder_chrome, f"no encoder connection for {gone:.0f}s")
            else:
                quiet = now - hub.encoder_last_msg
                if hub.encoder_last_msg and quiet > ENCODER_STALL_AFTER:
                    hub.encoder_gone_since = now
                    await asyncio.to_thread(
                        restart_encoder_chrome,
                        f"encoder connected but silent for {quiet:.0f}s")
        except Exception as exc:  # never let the watchdog die
            log(f"  supervisor error: {type(exc).__name__}: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    d = display()
    log(f"  display : {d.width:g}x{d.height:g} pts")
    trusted = await asyncio.to_thread(_trusted)
    if trusted is None:
        log("  helper  : UNREACHABLE -- is com.sandesh.cptr-input running?")
    else:
        log(f"  helper  : {'trusted' if trusted else 'NOT TRUSTED -- clicks will vanish'}")
    _reap_stale_profiles()
    start_encoder_chrome()
    supervisor = asyncio.create_task(_supervisor())
    log(f"  open    : http://127.0.0.1:{PORT}/")
    try:
        yield
    finally:
        supervisor.cancel()
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
    log(f"  [client] {payload.get('msg')}")
    return {"ok": True}


@app.get("/status")
async def status() -> dict:
    d = display()
    now = time.monotonic()
    return {
        "viewers": len(hub.viewers),
        "encoder_connected": hub.encoder is not None,
        # only meaningful while an encoder is attached -- it used to stay True
        # after a drop, which is what made a frozen stream look healthy
        "have_keyframe": hub.encoder is not None and hub.last_keyframe is not None,
        "codec": hub.codec,
        "helper_trusted": await asyncio.to_thread(_trusted),
        "display": {"width": d.width, "height": d.height},
        "encoder_uptime_s": round(now - hub.encoder_since, 1) if hub.encoder_since else None,
        "encoder_silent_s": round(now - hub.encoder_last_msg, 1) if hub.encoder_last_msg else None,
        "encoder_down_s": round(now - hub.encoder_gone_since, 1) if hub.encoder is None else None,
        "last_encoder_disconnect": hub.last_disconnect,
        "encoder_connects": hub.encoder_connects,
        "encoder_restarts": hub.restarts,
        "packets_relayed": hub.packets,
        "viewer_drops": sum(v.dropped for v in hub.viewers),
        "input_dropped": _input_dropped,
    }


@app.websocket("/ws/encoder")
async def ws_encoder(ws: WebSocket) -> None:
    await ws.accept()
    hub.encoder = ws
    hub.encoder_since = hub.encoder_last_msg = time.monotonic()
    hub.encoder_connects += 1
    log(f"  encoder connected (#{hub.encoder_connects})")
    reason = "clean exit"
    try:
        while True:
            message = await ws.receive()
            hub.encoder_last_msg = time.monotonic()
            if message.get("type") == "websocket.disconnect":
                code = message.get("code")
                reason = f"close code={code}" + (
                    "  <- server-side keepalive ping timeout" if code == 1011 else "")
                break
            if (text := message.get("text")) is not None:
                if text == "hb":            # liveness heartbeat, nothing to relay
                    continue
                # codec announcement -- viewers cannot decode without it
                hub.codec = text
                log(f"  codec: {text}")
                hub.broadcast_text(text)
                continue
            packet = message.get("bytes")
            if packet:
                hub.broadcast(packet)
                if hub.due_for_keyframe():
                    await hub.request_keyframe()
    except WebSocketDisconnect as exc:
        reason = _why(exc)
    except Exception as exc:
        reason = _why(exc)
    finally:
        # Only retract OUR registration. The page reconnects in ~1s, and if this
        # handler is slow to unwind it would otherwise null out the live socket
        # that replaced it -- leaving encoder_connected False forever.
        if hub.encoder is ws:
            hub.encoder = None
            hub.encoder_since = None
            hub.last_keyframe = None
            hub.encoder_gone_since = time.monotonic()
        hub.last_disconnect = reason
        log(f"  encoder disconnected: {reason}")


@app.websocket("/ws/viewer")
async def ws_viewer(ws: WebSocket) -> None:
    await ws.accept()
    viewer = Viewer(ws)
    hub.viewers.add(viewer)
    log(f"  viewer connected ({len(hub.viewers)} total) from {ws.client}")
    viewer.task = asyncio.create_task(viewer.pump())

    if hub.codec:
        viewer.offer(hub.codec)         # decoder config must arrive first
    await hub.request_keyframe()
    if hub.last_keyframe:
        viewer.offer(hub.last_keyframe)

    reason = "clean exit"
    try:
        while True:
            recv = asyncio.ensure_future(ws.receive_json())
            done, _ = await asyncio.wait(
                {recv, viewer.task}, return_when=asyncio.FIRST_COMPLETED)
            if viewer.task in done:      # the socket died under the writer
                recv.cancel()
                exc = None if viewer.task.cancelled() else viewer.task.exception()
                reason = _why(exc) if exc else "writer finished"
                break
            data = recv.result()
            kind = str(data.pop("kind", ""))
            data.pop("buttons_held", None)
            if kind not in {"pointer", "wheel", "key", "paste", "text"}:
                continue
            _enqueue_input(kind, data)
    except WebSocketDisconnect as exc:
        reason = _why(exc)
    except Exception as exc:
        reason = _why(exc)
    finally:
        hub.viewers.discard(viewer)
        if viewer.task and not viewer.task.done():
            viewer.task.cancel()
        log(f"  viewer disconnected ({len(hub.viewers)} left): {reason}"
            f" [sent={viewer.sent} dropped={viewer.dropped}]")


if __name__ == "__main__":
    import uvicorn

    # 127.0.0.1, not "::" and not "0.0.0.0". "::" on macOS binds IPv6-ONLY and
    # kills IPv4 access -- newt (Pangolin's tunnel target is 127.0.0.1:38218
    # explicitly; "localhost" makes newt dial [::1] and 502) needs IPv4. But
    # 0.0.0.0 goes further than newt needs: it also answers on the LAN interface,
    # with no auth at that layer -- this server has zero of its own (SSO is
    # Pangolin's job, entirely in front of the tunnel), so 0.0.0.0 meant any
    # device on the same wifi got unauthenticated mouse/keyboard control of this
    # Mac. 127.0.0.1 satisfies newt (same machine, connects over loopback) and
    # closes that off -- the ONLY way in is through the SSO'd tunnel.
    uvicorn.run(
        app, host="127.0.0.1", port=PORT, log_level="info",
        # The queued fan-out already keeps handlers responsive, but a viewer on a
        # bad link can still be slow to pong. 20s was tight enough to kill live
        # connections; 60s still notices a genuinely dead peer within ~80s.
        ws_ping_interval=20.0, ws_ping_timeout=60.0,
        log_config={
            "version": 1, "disable_existing_loggers": False,
            "formatters": {"t": {"format": "%(asctime)s  %(message)s",
                                 "datefmt": "%H:%M:%S"}},
            "handlers": {"h": {"class": "logging.StreamHandler", "formatter": "t",
                               "stream": "ext://sys.stderr"}},
            "loggers": {
                "uvicorn": {"handlers": ["h"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"handlers": ["h"], "level": "INFO", "propagate": False},
                "uvicorn.access": {"handlers": ["h"], "level": "INFO", "propagate": False},
            },
        },
    )
