# Desktop streaming — your Mac in a browser tab

Part of the [`cptr-input`](..) tool: streams this Mac's screen to a browser and
lets you control it — mouse, keyboard, scroll. No native app on the viewing
device. Open one URL from a phone, a laptop, or a cptr tab. Uses the
`cptr-input` daemon one directory up for the actual click/keystroke injection.

    encoder Chrome  --H.264-->  desktop_server  --H.264-->  your browser
      (this Mac)                   (relay)                   (anywhere)
                                      ^                          |
                                      +------ input events <-----+
                                      |
                               cptr-input helper (CGEvent -> the OS)

## Run

Installed as a launchd service (`com.sandesh.agent-desktop`) that starts on
login and restarts on crash. It captures headless — no picker, no clicks.

    launchctl kickstart -k gui/$(id -u)/com.sandesh.agent-desktop   # restart
    tail -f /tmp/agent-desktop.log                                  # logs

Locally: <http://127.0.0.1:38218/>. On the same wifi: `http://<mac-ip>:38218/`.
Everywhere: the Pangolin domain (below), which also gives you SSO.

## The one-letter bug that cost days

Headless screen capture works ONLY if Chrome's `--auto-select-desktop-capture-source`
value is a **regex that matches the picker's source label exactly**. On this
machine that label is **`Entire screen`** — lowercase `s`.

    --auto-select-desktop-capture-source=Entire screen     WORKS
    --auto-select-desktop-capture-source=Entire Screen     picker blocks -> hang
    --auto-select-desktop-capture-source=[Ss]creen         picker blocks -> hang
    --auto-select-desktop-capture-source=.                 selects a WINDOW, not the screen

When the value does not match, Chrome shows the manual picker. A human clicks
"Entire screen" and it works — which is exactly why it looked fine interactively
but hung under launchd, where nobody clicks. The earlier "responsible process /
TCC" theory was wrong: Chrome had the Screen Recording grant all along; the
picker was simply blocking.

If a future macOS/Chrome renames the source, re-find it:

    # tests/find_source.py launches Chrome with a candidate regex and reports
    # whether getDisplayMedia resolved and what surface/label it got.

## Files

| file | role |
|---|---|
| `desktop_server.py` | FastAPI relay: fans one encoder stream to many viewers; **production** input path imports `client.py` one directory up (`../client.py`, the neutral cptr-input daemon client) and calls `dispatch(kind, data)` over the unix socket |
| `static/encoder.html` | runs in the headless Chrome; captures the screen, encodes H.264 (WebCodecs) |
| `static/viewer.html`  | the page you open; decodes H.264, sends pointer/key/wheel |
| `mapper.py` | cptr-style input message -> macOS CGEvent parameters (pure, testable) |
| `mapper_client.py` | a **second, lower-level** client used only by `tests/verify_helper.py` — talks to the same `cptr-input` unix socket but takes a `Display` + planned `mapper.py` events instead of raw daemon ops. Kept for the on-device verification harness (captures + times injected events); not used by `desktop_server.py`. |
| `inject.py` | test-only: posts CGEvents directly (bypassing the daemon) for `tests/verify_*.py` |
| `tests/` | the staged verification scripts (simulate, warp, post, capture, the real `mapper_client.py` end-to-end proof) |

## How input works (and the trap)

Clicks go to a **separate signed helper**, `cptr-input.app`, over a unix socket.
It exists because macOS keys Accessibility to a code signature, and cptr's /
this app's Python is ad-hoc signed — every `uv` upgrade would silently drop the
grant and clicks would vanish with no error. The helper's bundle id is stable,
so it is granted once and stays granted.

Grant is checked at `/status` (`helper_trusted`). TCC caches trust at process
start, so after granting you must restart the helper:

    launchctl kickstart -k gui/$(id -u)/com.sandesh.cptr-input

## Codec

The encoder probes `VideoEncoder.isConfigSupported()` down a list of H.264 levels
and announces the winner to viewers, who configure their decoder from it. This is
required: a fixed codec string encodes a level that caps resolution, and an
ultrawide desktop (2560x1080 = 10800 macroblocks) exceeds level 4.0's 8192, so a
hardcoded `avc1.42E028` would throw. Measured: **2560x1080 @ ~28fps, ~1.8 Mb/s**.

## Exposing it (Pangolin + SSO)

Add a Pangolin resource for the desktop domain pointing at **`127.0.0.1:38218`**
(NOT `localhost:38218` — newt resolves localhost to `[::1]` first and 502s).
Pangolin's SSO then gates the whole thing, so the stream is not public.

WebSockets pass through Pangolin fine (cptr's terminals prove it), so no special
config is needed beyond the resource.

## Robustness fixes (learned the hard way)

- **Display probed live, never cached.** The user can change resolution or swap
  monitors while the service runs; `display()` re-probes every call so clicks
  always map to the current screen. A stale cache sent input to the wrong place.
- **Even capture dimensions.** A non-16:9 display gave 1671x1080 (odd width),
  which every H.264 level rejects. The encoder rounds width/height down to even.
- **Input client auto-reconnects.** The helper restarts (upgrades, re-grant); the
  persistent socket would otherwise stay broken and clicks silently stop. `send()`
  reconnects once on a dead socket, with a 5s timeout so it never hangs.
- **Helper is concurrent.** One stuck connection used to wedge the whole daemon
  (single-threaded accept loop). Each connection now runs on its own queue with a
  30s recv timeout.
- **Dispatch is serialized, not thread-pooled.** Sharing one helper socket across
  `run_in_executor` workers corrupted its I/O and scrambled coordinates. Input is
  now dispatched directly under a lock.
