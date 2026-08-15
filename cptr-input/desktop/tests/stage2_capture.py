"""Stage 2: can Chrome capture the whole desktop headlessly, with a cursor?

Two unknowns this settles:
  1. Does --auto-select-desktop-capture-source bypass the picker for a MONITOR
     (cptr only ever proved it for a tab, via --auto-select-tab-capture-source-by-title)?
  2. Does the captured stream include the mouse cursor? Without it, remote control
     is unusable -- you would be clicking blind. cptr's browser-encoder.js never
     sets the `cursor` constraint.

Serves a tiny page locally, launches Chrome with a throwaway profile, and has the
page report track settings back. Chrome needs Screen Recording permission; the
first run may raise a system prompt.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

RESULT: dict = {}
DONE = threading.Event()

PAGE = """<!doctype html><meta charset=utf-8><title>cptr desktop capture spike</title>
<body><h2>capturing…</h2><script>
async function go() {
  const out = {};
  try {
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: { displaySurface: 'monitor', cursor: 'always' }, audio: false
    });
    const track = stream.getVideoTracks()[0];
    const s = track.getSettings();
    out.ok = true;
    out.displaySurface = s.displaySurface;
    out.width = s.width; out.height = s.height;
    out.frameRate = s.frameRate;
    out.cursor = s.cursor === undefined ? 'not-reported' : s.cursor;
    out.label = track.label;
    // grab one frame to prove pixels actually flow (not a black stream)
    const vid = document.createElement('video');
    vid.srcObject = stream; await vid.play();
    await new Promise(r => setTimeout(r, 600));
    const c = document.createElement('canvas');
    c.width = 160; c.height = 100;
    c.getContext('2d').drawImage(vid, 0, 0, 160, 100);
    const data = c.getContext('2d').getImageData(0, 0, 160, 100).data;
    let sum = 0, mn = 255, mx = 0;
    for (let i = 0; i < data.length; i += 4) { sum += data[i]; mn = Math.min(mn, data[i]); mx = Math.max(mx, data[i]); }
    out.meanLuma = Math.round(sum / (data.length / 4));
    out.minLuma = mn; out.maxLuma = mx;
    out.nonBlank = mx - mn > 8;
    out.webcodecs = !!window.VideoEncoder;
    stream.getTracks().forEach(t => t.stop());
  } catch (e) {
    out.ok = false; out.error = String(e && e.name) + ': ' + String(e && e.message);
  }
  await fetch('/result', {method: 'POST', body: JSON.stringify(out)});
  document.body.innerHTML = '<h2>done</h2>';
}
go();
</script></body>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        RESULT.update(json.loads(self.rfile.read(n) or b"{}"))
        self.send_response(204)
        self.end_headers()
        DONE.set()


def find_chrome() -> str | None:
    for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"):
        if shutil.which(p) or __import__("os").path.exists(p):
            return p
    return None


def main() -> int:
    chrome = find_chrome()
    if not chrome:
        print("  no Chrome-family browser found")
        return 2
    print(f"  browser : {chrome}")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    profile = tempfile.mkdtemp(prefix="cptr-spike-")
    args = [
        chrome,
        f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        "--disable-background-networking",
        # the unknown we are testing: auto-pick the whole screen, no picker UI
        "--auto-select-desktop-capture-source=[Ee]ntire [Ss]creen|[Ss]creen",
        "--auto-accept-this-tab-capture",
        f"http://127.0.0.1:{port}/",
    ]
    print("  flag    : --auto-select-desktop-capture-source='Entire Screen'")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ok = DONE.wait(timeout=45)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    shutil.rmtree(profile, ignore_errors=True)

    if not ok:
        print("  TIMEOUT -- no result posted back (picker may have blocked, or "
              "Screen Recording permission was never granted)")
        return 1

    print()
    if not RESULT.get("ok"):
        print(f"  capture FAILED: {RESULT.get('error')}")
        print("  -> if this is NotAllowedError, the flag did not bypass the picker")
        return 1

    for k in ("displaySurface", "width", "height", "frameRate", "cursor",
              "meanLuma", "minLuma", "maxLuma", "nonBlank", "webcodecs", "label"):
        if k in RESULT:
            print(f"    {k:15s}: {RESULT[k]}")
    print()
    print(f"  desktop capture : {'WORKS' if RESULT.get('displaySurface') == 'monitor' else 'WRONG SURFACE'}")
    print(f"  real pixels     : {'yes' if RESULT.get('nonBlank') else 'NO -- black frame'}")
    print(f"  cursor drawn    : {RESULT.get('cursor')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
