"""Runs on aibo-linux (piped after scrub.py into `python3 - LO HI`). Collects what lives there, scrubs it with
aibo-linux's own keys.env, and prints one JSON document on stdout:

  messages  crew messages LO..HI (report, ask + answer, start, relay)
  chats     cptr session id -> {chat_id, title}, for provenance
  sessions  ai-memory session files written on aibo-linux (Dir under /home/), LO..HI by the file's date; with
            --since-mtime (the nightly run), every one written since LO, whole (the librarian reads the new turns)
  memories  Claude auto-memory files of aibo-linux projects (not with --since-mtime)
Read-only everywhere.
"""
import glob, json, os, re, sqlite3, sys
from datetime import datetime

LO, HI = sys.argv[1], sys.argv[2]
BY_MTIME = "--since-mtime" in sys.argv
H = os.path.expanduser("~")
out = {"messages": [], "chats": {}, "sessions": {}, "memories": {}}

db = f"{H}/Documents/personal/agent-tools/messaging/data/messaging.db"
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
lo = int(datetime.fromisoformat(LO).timestamp() * 1000)
hi = int(datetime.fromisoformat(HI).timestamp() * 1000)
for ts, frm, to, kind, body, reply, conv in c.execute(
        "select ts, frm_label, to_label, kind, body, reply, conversation from messages "
        "where ts>=? and ts<? and kind in ('report','ask','start','relay') order by ts", (lo, hi)):
    out["messages"].append({"ts": ts, "from": scrub(frm or ""), "to": scrub(to or ""), "kind": kind,
                            "body": scrub((body or "")[:1500]), "reply": scrub((reply or "")[:800])})

try:
    cp = sqlite3.connect(f"file:{H}/.cptr/app.db?mode=ro", uri=True)
    for cid, title, meta in cp.execute("select id, title, meta from chats"):
        m = json.loads(meta or "{}")
        for prof, s in (m.get("agent_sessions") or {}).items():
            if s.get("session_id"):
                out["chats"][s["session_id"][:8]] = {"chat_id": cid, "title": scrub(title), "machine": "aibo-linux"}
except Exception as e:
    print(f"cptr map: {e}", file=sys.stderr)

lo_d, hi_d = LO[:10].replace("-", ""), HI[:10].replace("-", "")
for f in glob.glob(f"{H}/Documents/personal/ai-memory/sessions/*.md"):
    m = re.search(r"-(\d{8})-\d{4}\.md$", f)
    if not m:
        continue
    if BY_MTIME:
        if os.path.getmtime(f) < datetime.fromisoformat(LO).timestamp() - 3600:
            continue
    elif not (lo_d <= m.group(1) < hi_d):
        continue
    s = open(f, errors="replace").read()
    d = re.search(r"\*\*Dir:\*\* (\S+)", s)
    if d and d.group(1).startswith("/home/"):
        out["sessions"][os.path.basename(f)] = scrub(s)

for f in [] if BY_MTIME else glob.glob(f"{H}/Documents/personal/ai-memory/memories/claude/-home-*/*.md"):
    out["memories"][f.split("/memories/claude/", 1)[1]] = scrub(open(f, errors="replace").read())

print(json.dumps(out))
print(f"remote scrub hits: {HITS}", file=sys.stderr)
