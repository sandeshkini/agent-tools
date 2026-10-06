#!/usr/bin/env python3
"""Export the shared agent memory to Markdown, so the long-term record is plain files in git.

Runs on aibo-linux (systemd --user timer `memory-export`, nightly). Reads FalkorDB inside the
`memory` container and writes, under ~/Documents/ai-memory/knowledge/<group>/:

  episodes/YYYY-MM.md   every episode (the raw text an agent or Sandesh recorded, who/when).
                        THIS is the source of truth: the graph can be rebuilt from it.
  facts.md              facts that are true now (valid, not superseded), with their source episode
  superseded.md         facts that were later contradicted/invalidated, newest first

Then commits in the ai-memory repo (backup.sh pushes it nightly). Stdlib only.

  python3 memory/export.py [--group personal] [--out ~/Documents/ai-memory/knowledge] [--no-commit]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path

QUERY = r'''
import json, sys
from falkordb import FalkorDB
g = FalkorDB(host="localhost", port=6379).select_graph(sys.argv[1])
def rows(q):
    try:
        return g.query(q).result_set
    except Exception as e:
        return []
eps = rows("MATCH (e:Episodic) RETURN e.uuid, e.name, e.content, e.source, e.source_description, toString(e.created_at), toString(e.valid_at) ORDER BY e.created_at")
facts = rows("MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) RETURN r.uuid, a.name, r.name, b.name, r.fact, toString(r.valid_at), toString(r.invalid_at), toString(r.expired_at), toString(r.created_at), r.episodes ORDER BY r.created_at")
print(json.dumps({"episodes": eps, "facts": facts}, default=str))
'''


def load(group: str) -> dict:
    out = subprocess.run(["docker", "exec", "-i", "memory", "/app/mcp/.venv/bin/python", "-", group], input=QUERY,
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def day(ts: str | None) -> str:
    return (ts or "")[:10] or "unknown"


def clean(s) -> str:
    s = "" if s in (None, "None", "null") else str(s)
    return s.strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="personal")
    ap.add_argument("--out", default=str(Path.home() / "Documents/ai-memory/knowledge"))
    ap.add_argument("--no-commit", action="store_true")
    a = ap.parse_args()

    data = load(a.group)
    root = Path(a.out).expanduser() / a.group
    (root / "episodes").mkdir(parents=True, exist_ok=True)
    names = {}

    by_month: dict[str, list] = defaultdict(list)
    for uuid, name, content, source, desc, created, valid in data["episodes"]:
        names[uuid] = clean(name)
        by_month[day(created)[:7]].append((uuid, name, content, source, desc, created, valid))
    for month, eps in by_month.items():
        lines = [f"# Memory episodes · {a.group} · {month}", "",
                 "Raw records as written by agents / Sandesh. Source of truth for the memory graph.", ""]
        for uuid, name, content, source, desc, created, valid in eps:
            lines += [f"## {clean(name) or uuid}", "",
                      f"- **recorded:** {day(clean(created))}" + (f" · **about:** {day(clean(valid))}" if clean(valid) and day(clean(valid)) != day(clean(created)) else ""),
                      f"- **source:** {clean(desc) or clean(source)}",
                      f"- **id:** `{uuid}`", "", clean(content), ""]
        (root / "episodes" / f"{month}.md").write_text("\n".join(lines) + "\n")

    current, superseded = [], []
    for uuid, a_name, rel, b_name, fact, valid, invalid, expired, created, eps in data["facts"]:
        src = ", ".join(names.get(e, e[:8]) for e in (eps or []))
        row = (clean(fact), clean(a_name), clean(b_name), clean(valid), clean(invalid), clean(expired), clean(created), src)
        (superseded if (row[4] or row[5]) else current).append(row)

    cur = [f"# What memory holds as true now · {a.group}", "",
           f"{len(current)} facts. Generated from the graph; edit by recording a correction, not here.", ""]
    for fact, an, bn, valid, _, _, created, src in sorted(current, key=lambda r: r[1].lower()):
        cur.append(f"- {fact}  \n  <sub>{an} → {bn} · since {day(valid or created)} · from: {src}</sub>")
    (root / "facts.md").write_text("\n".join(cur) + "\n")

    sup = [f"# Superseded facts · {a.group}", "",
           "Facts that were true once and later contradicted or invalidated (kept as history).", ""]
    for fact, an, bn, valid, invalid, expired, created, src in sorted(superseded, key=lambda r: r[4] or r[5], reverse=True):
        sup.append(f"- ~~{fact}~~  \n  <sub>{day(valid or created)} → {day(invalid or expired)} · from: {src}</sub>")
    (root / "superseded.md").write_text("\n".join(sup) + "\n")

    print(f"{a.group}: {len(data['episodes'])} episodes, {len(current)} current facts, {len(superseded)} superseded → {root}")
    if not a.no_commit:
        repo = Path(a.out).expanduser().parent
        subprocess.run(["git", "-C", str(repo), "add", str(root)], check=False)
        r = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--quiet"])
        if r.returncode:
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", f"memory export ({a.group})"], check=False)
            print("committed")


if __name__ == "__main__":
    main()
