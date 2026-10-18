"""Fold a hand-kept doc into the wiki: one librarian pass (extract, route, apply) over a single file, so each of
its lines either confirms a fact that's already there (adding the doc as a source) or becomes a new fact. Used to
fold the operating manual (how Sandesh works) into his page, so the wiki is the one copy.

  python3 -I fold_doc.py <file> [--spoke "his words, gathered by agents"] [--dry]

--dry stops after extract and prints the candidates. Model and plan as the nightly run (core.py).
"""
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core as R
from scrub import scrub, leaks

GUIDE = """This source is a hand-kept doc of how Sandesh works (his preferences and rules, gathered by his agents
from his own words; quotes in it are his). Treat each line as kind "relayed" (his words, passed on), dated
{date}. Rules and preferences for all agents go on person/sandesh; design taste can go on the design page and money
views on the money page if those fit better. Write one candidate per rule; skip lines that are now history (for
example a tool that was retired) unless they say why.

"""


def main():
    args = sys.argv[1:]
    path = os.path.abspath(os.path.expanduser(args[0]))
    spoke = args[args.index("--spoke") + 1] if "--spoke" in args else "his words, gathered by agents"
    label = "F" + time.strftime("%Y-%m-%d")
    date = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))
    pages, claims = R.load()
    before = {cid: c["status"] for cid, c in claims.items()}
    n = max([int(k[1:]) for k in R.SOURCES if re.fullmatch(r"S\d+", k)] or [0]) + 1
    ref = f"S{n:04d}"
    rel = path.replace(os.path.expanduser("~"), "~")
    R.SOURCES[ref] = {"type": "doc", "agent": os.path.basename(path), "machine": "aibo-mac", "date": date,
                      "path": rel, "spoke": spoke, "night": label}
    text = GUIDE.format(date=date) + f"=== {ref} · doc · {os.path.basename(path)} · aibo-mac · {date}\n" + scrub(open(path).read())
    cands = R.extract(label, pages, claims, text)
    if "--dry" in args:
        print(json.dumps(cands, indent=1, ensure_ascii=False))
        return
    cands = R.route(label, cands, pages, claims) if cands else []
    stats = R.apply(label, cands, pages, claims) if cands else {}
    for cid in [c for c in claims if c not in before]:
        if leaks(claims[cid]["text"]) or re.search(r"\[(secret[^\]]*|number|ip|email)\]", claims[cid]["text"]):
            del claims[cid]
    R.save(pages, claims)
    confirmed = [c["id"] for c in claims.values() if ref in (c.get("refs") or []) and c["id"] in before]
    added = [c["id"] for c in claims.values() if c["id"] not in before]
    rec = {"night": label, "doc": rel, "ref": ref, "candidates": len(cands), "ops": stats, "confirmed": len(confirmed),
           "added": len(added), "calls": len(R.runs), "tokens_in": sum(r["in"] for r in R.runs),
           "tokens_out": sum(r["out"] for r in R.runs), "usd_equiv": round(sum(r.get("usd_equiv") or 0 for r in R.runs), 2)}
    nights = json.load(open(f"{R.DATA}/nights.json")) if os.path.exists(f"{R.DATA}/nights.json") else []
    R.write_atomic(f"{R.DATA}/nights.json", json.dumps(nights + [rec], indent=1))
    print(json.dumps(rec))
    for cid in added:
        c = claims[cid]
        print(f"+ {cid} {c['page']} [{c['section']}] {c['text'][:150]}")


if __name__ == "__main__":
    main()
