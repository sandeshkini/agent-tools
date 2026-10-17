#!/usr/bin/env python3
"""Nightly records job on aibo-mac: back up the two databases that have no other copy, and export
the crew messages and card answers into ai-memory. Stdlib only. Run as the pa-app "Records Backup".

1. Snapshots. cos.db (inside the hub container) and cptr's app.db are copied with VACUUM INTO on a
   read-only connection: a consistent copy, without stopping or writing to either app. Each copy
   passes `pragma integrity_check`, is gzipped, and goes to aibo-linux under
   ~/Documents/backups/aibo-mac/<date>/, which aibo-linux's nightly backup.sh syncs to B2
   (b2crypt:aibo-mac). aibo-mac has no B2 of its own. The last 7 days are kept there, 3 here.
2. Records. Into ai-memory/records/, one JSONL file per month, merged by id so that rows the live
   databases later delete stay in the record:
   - crew/YYYY-MM.jsonl   every crew message (messaging.db on aibo-linux)
   - cards/YYYY-MM.jsonl  every card and his answer (from tonight's cos.db snapshot)
   Text is scrubbed twice: on aibo-linux with its keys.env, then here with this Mac's (scrub.py).
   sync-repos (data policy) commits and pushes ai-memory.

    nightly.py              everything
    nightly.py --only snapshots | records
    nightly.py --restore-check <dir>   open every *.db.gz in a backup folder and compare with its manifest
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scrub  # noqa: E402

HOME = Path.home()
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")
STAGE = HOME / ".local/share/records-backup"          # outside ~/Documents: no privacy prompt needed
AI_MEMORY = HOME / "Documents/personal/ai-memory"
CPTR_DB = HOME / ".cptr/app.db"
HUB = "cos-hub-1"
HUB_DB = "/data/cos.db"
LINUX = "aibo-linux"
REMOTE_DIR = "Documents/backups/aibo-mac"               # relative to aibo-linux's home
MESSAGING_DB = "/home/beastblaster/Documents/personal/agent-tools/messaging/data/messaging.db"
KEEP_LOCAL, KEEP_REMOTE = 3, 7
# Its own key and no agent: under launchd the 1Password SSH agent would prompt or hang.
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "IdentityAgent=none", "-o", "ConnectTimeout=15",
       "-i", str(HOME / ".ssh/id_ed25519")]


def log(msg):
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, **kw)
    if r.returncode:
        raise RuntimeError(f"{cmd[0]} exited {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect(db: Path) -> dict:
    s = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        ok = s.execute("pragma integrity_check").fetchone()[0]
        tables = [r[0] for r in s.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
        counts = {t: s.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
    finally:
        s.close()
    return {"integrity": ok, "tables": counts}


# ---------- 1. snapshots ----------

def snapshot_hub(dest: Path):
    tmp = "/tmp/records-backup-cos.db"
    code = ("import sqlite3,os\n"
            f"p={tmp!r}\n"
            "os.path.exists(p) and os.remove(p)\n"
            f"s=sqlite3.connect('file:{HUB_DB}?mode=ro',uri=True)\n"
            "s.execute(\"VACUUM INTO '\"+p+\"'\")\n")
    run(["docker", "exec", HUB, "python3", "-c", code])  # read-only: no hub restart, no idle gate needed
    try:
        run(["docker", "cp", f"{HUB}:{tmp}", str(dest)])
    finally:
        subprocess.run(["docker", "exec", HUB, "rm", "-f", tmp], capture_output=True)


def snapshot_cptr(dest: Path):
    s = sqlite3.connect(f"file:{CPTR_DB}?mode=ro", uri=True)
    try:
        s.execute("VACUUM INTO ?", (str(dest),))
    finally:
        s.close()


def snapshots(day: str) -> Path:
    out = STAGE / day
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    manifest = {"made": dt.datetime.now().isoformat(timespec="seconds"), "machine": "aibo-mac", "files": {}}
    for name, fn, src in (("cos.db", snapshot_hub, f"{HUB}:{HUB_DB}"), ("cptr-app.db", snapshot_cptr, str(CPTR_DB))):
        db = out / name
        fn(db)
        info = inspect(db)
        if info["integrity"] != "ok":
            raise RuntimeError(f"{name}: integrity_check said {info['integrity']}")
        gz = out / f"{name}.gz"
        with open(db, "rb") as f, gzip.open(gz, "wb", compresslevel=6) as g:
            shutil.copyfileobj(f, g)
        manifest["files"][gz.name] = {"source": src, "bytes": db.stat().st_size, "sha256_db": sha256(db),
                                      "sha256_gz": sha256(gz), **info}
        log(f"snapshot {name}: {db.stat().st_size} bytes, {sum(info['tables'].values())} rows, gz {gz.stat().st_size}")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    for old in sorted(p for p in STAGE.iterdir() if p.is_dir())[:-KEEP_LOCAL]:
        shutil.rmtree(old)
    return out


def push(out: Path, day: str):
    remote = f"{REMOTE_DIR}/{day}"
    run(SSH + [LINUX, f"mkdir -p {remote}.part && rm -f {remote}.part/*"])
    files = sorted(p for p in out.iterdir() if p.suffix in (".gz", ".json"))
    run(["scp", "-q", *SSH[1:], *map(str, files), f"{LINUX}:{remote}.part/"])
    sums = run(SSH + [LINUX, f"cd {remote}.part && sha256sum *.gz"])
    want = json.loads((out / "manifest.json").read_text())["files"]
    got = {line.split()[1]: line.split()[0] for line in sums.splitlines()}
    for name, meta in want.items():
        if got.get(name) != meta["sha256_gz"]:
            raise RuntimeError(f"push: checksum of {name} on {LINUX} doesn't match")
    # Swap in only a complete, checked folder, then keep the newest KEEP_REMOTE days.
    run(SSH + [LINUX, f"rm -rf {remote} && mv {remote}.part {remote} && "
                      f"cd {REMOTE_DIR} && ls -1d 20??-??-?? | head -n -{KEEP_REMOTE} | xargs -r rm -rf"])
    log(f"pushed {len(files)} files to {LINUX}:~/{remote} (checksums match)")


# ---------- 2. records ----------

def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds") if ms else None


def merge_month_files(folder: Path, rows: list[dict], ts_key: str) -> int:
    """Write rows into folder/YYYY-MM.jsonl, merged by id with what's there (nothing is dropped)."""
    folder.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[dict]] = {}
    for r in rows:
        by_month.setdefault((r.get(ts_key) or "0000-00")[:7], []).append(r)
    changed = 0
    for month, new in by_month.items():
        f = folder / f"{month}.jsonl"
        have = {}
        if f.exists():
            for line in f.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    have[r["id"]] = r
        before = dict(have)
        for r in new:
            have[r["id"]] = r
        if have != before:
            body = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                           for r in sorted(have.values(), key=lambda r: (r.get(ts_key) or "", r["id"])))
            tmp = f.with_suffix(".tmp")
            tmp.write_text(body)
            tmp.replace(f)
            changed += 1
    return changed


REMOTE_CREW = r'''
import json, sqlite3, sys
s = sqlite3.connect("file:%s?mode=ro", uri=True)
s.row_factory = sqlite3.Row
cols = "id ts frm frm_label to_thread to_label kind body conversation reply_to hop status reply reply_ts for_thread".split()
out = []
for r in s.execute("select * from messages"):
    d = {c: r[c] for c in cols if c in r.keys()}
    for k in ("body", "reply", "frm_label", "to_label"):
        d[k] = scrub(d.get(k) or "")
    out.append(d)
json.dump(out, sys.stdout)
''' % MESSAGING_DB


def crew_rows() -> list[dict]:
    script = (HERE / "scrub.py").read_text() + "\n" + REMOTE_CREW
    raw = json.loads(run(SSH + [LINUX, "python3 -I -"], input=script))
    rows = []
    for m in raw:
        rows.append({
            "id": m["id"], "ts": iso(m["ts"]), "kind": m.get("kind"),
            "from": {"thread": m.get("frm"), "name": scrub.scrub(m.get("frm_label") or "")},
            "to": {"thread": m.get("to_thread"), "name": scrub.scrub(m.get("to_label") or "")},
            "for_thread": m.get("for_thread"), "conversation": m.get("conversation"), "reply_to": m.get("reply_to"),
            "hop": m.get("hop"), "status": m.get("status"),
            "body": scrub.scrub(m.get("body") or ""),
            "reply": scrub.scrub(m.get("reply") or "") or None, "reply_ts": iso(m.get("reply_ts")),
        })
    return rows


def card_rows(snapshot_dir: Path) -> list[dict]:
    db = snapshot_dir / "cos.db"
    s = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    s.row_factory = sqlite3.Row
    rows = []
    for c in s.execute("select * from cards"):
        def t(k):
            return scrub.scrub(c[k] or "") or None
        rows.append({
            "id": c["id"], "ts": iso(c["created_at"]), "updated": iso(c["updated_at"]),
            "chat": f"{c['node']}:{c['chat_id']}" if c["chat_id"] else None,
            "title": t("title"), "headline": t("headline"), "state": c["state"],
            "summary": t("summary"), "question": t("question"), "options": t("options"),
            "reply": t("reply"), "replied_at": iso(c["replied_at"]), "next": t("next"),
            "item_ref": c["item_ref"], "direct": c["direct"], "dismissed": c["dismissed"],
        })
    s.close()
    return rows


def records(snapshot_dir: Path):
    base = AI_MEMORY / "records"
    crew = crew_rows()
    n1 = merge_month_files(base / "crew", crew, "ts")
    cards = card_rows(snapshot_dir)
    n2 = merge_month_files(base / "cards", cards, "ts")
    # Final check: what lands in git holds no secret value or token pattern.
    for f in list((base / "crew").glob("*.jsonl")) + list((base / "cards").glob("*.jsonl")):
        hits = [h for h in scrub.leaks(f.read_text()) if h != "ip"]
        if hits:
            raise RuntimeError(f"records: {f.name} still matches {hits[:3]}")
    log(f"records: {len(crew)} crew messages ({n1} month files changed), {len(cards)} cards ({n2} changed); "
        f"scrubbed {scrub.HITS}")


# ---------- restore check ----------

def restore_check(folder: Path) -> bool:
    manifest = json.loads((folder / "manifest.json").read_text())
    ok = True
    for name, meta in manifest["files"].items():
        gz = folder / name
        if sha256(gz) != meta["sha256_gz"]:
            print(f"{name}: gz checksum differs"); ok = False; continue
        db = folder / name[:-3]
        with gzip.open(gz, "rb") as g, open(db, "wb") as f:
            shutil.copyfileobj(g, f)
        info = inspect(db)
        same = sha256(db) == meta["sha256_db"] and info == {"integrity": meta["integrity"], "tables": meta["tables"]}
        print(f"{name}: integrity {info['integrity']}, {len(info['tables'])} tables, "
              f"{sum(info['tables'].values())} rows, {'matches' if same else 'DIFFERS FROM'} the manifest")
        ok &= same and info["integrity"] == "ok"
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["snapshots", "records"])
    ap.add_argument("--restore-check", type=Path)
    a = ap.parse_args()
    if a.restore_check:
        sys.exit(0 if restore_check(a.restore_check) else 1)
    day = dt.date.today().isoformat()
    failed = False
    out = STAGE / day
    if a.only in (None, "snapshots"):
        try:
            out = snapshots(day)
            push(out, day)
        except Exception as e:  # keep going: the records step is independent
            log(f"ERROR snapshots: {e}"); failed = True
    if a.only in (None, "records"):
        try:
            if not (out / "cos.db").exists():
                out = snapshots(day)
            records(out)
        except Exception as e:
            log(f"ERROR records: {e}"); failed = True
    # Keep only the gz copies locally; the plain .db files were for checks and the card export.
    for p in STAGE.glob("*/*.db"):
        p.unlink()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
