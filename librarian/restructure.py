"""Librarian lint, split step: a page with more than LIMIT current claims is split into sub-pages.

The model proposes the sub-pages and assigns every claim (current and history) to one of them or keeps it on the
parent. Claim ids, provenance and history are unchanged; only `page` moves. Sub-pages record their `parent`, and the
parent links to them.

  python3 -I restructure.py            # every page over the limit
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import core as R

LIMIT = 30
PROMPT = """This wiki page has grown to cover too many things. Split it into sub-pages, each about one thing that an
agent would look up on its own (for example the board, cards and messages, groups and leads, the iOS app, mini apps,
deploys). Aim for 5 to 9 sub-pages of 8 to 25 claims each. Keep on the parent only the claims that are about the whole
thing.

Sub-page ids use an allowed prefix and the parent's slug, for example `project/cos-board` or `system/cos-deploy`
(private parents stay private: `~sandesh/money-claims`). Every claim id below must appear exactly once in "assign"
(use the parent id to keep it there).

Answer with JSON only:
{{"parent_summary": "<one line>",
  "pages": [{{"id": "...", "title": "...", "summary": "<one line>"}}, ...],
  "assign": {{"sandesh:0042": "<page id>", ...}}}}

{page}

## all claims on the page (including history)
{claims}"""


def split(pid, pages, claims):
    cs = sorted([c for c in claims.values() if c["page"] == pid], key=lambda c: c["date"])
    lines = "\n".join(f"- [{c['id']}] {c['section']} · {c['status']} · {c['date']} · {c['text'][:220]}" for c in cs)
    out = R.parse_json(R.claude(PROMPT.format(page=R.page_text(pid, pages, claims), claims=lines), f"split {pid}"))
    made = []
    for p in out.get("pages", []):
        if R.PREFIX.match(p["id"]) and p["id"] not in pages and p["id"].startswith("~") == pid.startswith("~"):
            R.make_page(pages, p["id"], p["title"], p.get("summary", ""), "lint-split")
            pages[p["id"]]["parent"] = pid
            made.append(p["id"])
    moved = 0
    for cid, tgt in out.get("assign", {}).items():
        if cid in claims and claims[cid]["page"] == pid and tgt in pages and tgt != pid:
            claims[cid]["page"] = tgt
            moved += 1
    if out.get("parent_summary"):
        pages[pid]["summary"] = out["parent_summary"]
    pages[pid]["children"] = sorted(set(pages[pid].get("children", []) + made))
    return made, moved


def main():  # weekly.py runs this
    pages, claims = R.load()
    big = [p for p in pages if sum(1 for c in claims.values() if c["page"] == p and c["status"] == "current") > LIMIT]
    log = json.load(open(f"{R.DATA}/nights.json")) if os.path.exists(f"{R.DATA}/nights.json") else []
    for pid in big:
        made, moved = split(pid, pages, claims)
        print(pid, "->", made, "moved", moved)
        log.append({"night": "lint-split", "page": pid, "new_pages": made, "moved": moved})
    R.save(pages, claims)
    R.write_atomic(f"{R.DATA}/nights.json", json.dumps(log, indent=1))


if __name__ == "__main__":
    main()
