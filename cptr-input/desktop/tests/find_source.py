"""Find the auto-select regex that matches this Mac's desktop capture source.

macOS/Chrome can rename the whole-screen source label between versions. When the
encoder starts hanging (picker blocking), run this to find the value for
--auto-select-desktop-capture-source in desktop_server.py.

    ../.venv/bin/python find_source.py
"""
import subprocess, tempfile, threading, http.server, json, shutil, sys

CANDIDATES = ["Entire screen", "Entire Screen", "Screen 1", "Screen 2",
              "[Ee]ntire [Ss]creen", "Built-in", "Color LCD", "."]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PAGE = b"""<script>
navigator.mediaDevices.getDisplayMedia({video:{displaySurface:'monitor'}})
 .then(s=>{const t=s.getVideoTracks()[0];const g=t.getSettings();
   fetch('/r',{method:'POST',body:JSON.stringify({ok:1,label:t.label,surface:g.displaySurface})});t.stop()})
 .catch(e=>fetch('/r',{method:'POST',body:JSON.stringify({ok:0,err:e.name})}))
</script>"""

def test(rx):
    R={}; D=threading.Event()
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(s,*a): pass
        def do_GET(s): s.send_response(200); s.send_header('Content-Length',str(len(PAGE))); s.end_headers(); s.wfile.write(PAGE)
        def do_POST(s): n=int(s.headers['Content-Length']); R.update(json.loads(s.rfile.read(n))); s.send_response(204); s.end_headers(); D.set()
    srv=http.server.HTTPServer(('127.0.0.1',0),H); port=srv.server_address[1]
    threading.Thread(target=srv.serve_forever,daemon=True).start()
    prof=tempfile.mkdtemp(prefix="find-src")
    p=subprocess.Popen([CHROME,f"--user-data-dir={prof}","--no-first-run",
        f"--auto-select-desktop-capture-source={rx}","--window-position=-32000,-32000",
        f"http://127.0.0.1:{port}/"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    ok=D.wait(timeout=14); p.terminate(); shutil.rmtree(prof,ignore_errors=True)
    return R if ok else {"timeout":True}

if __name__ == "__main__":
    for rx in CANDIDATES:
        r=test(rx)
        good = r.get("ok")==1 and r.get("surface")=="monitor"
        print(f"  {rx!r:24} -> {r}  {'<-- USE THIS' if good else ''}")
