"""The librarian's weekly run: tidy the wiki and rewrite the pages that changed.

  1. tidy: a page with more than 32 current facts is consolidated (merges and supersedes, nothing deleted);
  2. split: a page still over 30 gets sub-pages (restructure.py);
  3. aliases: names that mean each page, for links and search (link.py);
  4. rewrite: every page whose facts changed since it was written gets fresh prose (write_wiki.py).

His fixes from cos are pinned all the way through: tidy and split never merge or replace them, and the page
writer keeps their wording. Model: Sonnet 5.5 at medium on his plan (claude-sandesh).

  python3 -I weekly.py           # all four
  python3 -I weekly.py --all     # rewrite every page, not only the changed ones
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core as R
import link
import restructure
import write_wiki


def main():
    t0 = time.time()
    label = time.strftime("W%Y-%m-%d")
    pages, claims = R.load()
    big, stats = R.tidy(pages, claims)
    R.save(pages, claims)
    print("tidy", big, stats, flush=True)
    restructure.main()
    link.main()
    written = write_wiki.main(everything="--all" in sys.argv)
    pages, claims = R.load()
    rec = {"night": label, "tidied": big, "ops": stats, "rewritten": len(written), "pages": len(pages),
           "current_claims": sum(1 for c in claims.values() if c["status"] == "current"),
           "calls": len(R.runs) + len(written),
           "tokens_in": sum(r["in"] for r in R.runs) + sum(w["in"] for w in written),
           "tokens_out": sum(r["out"] for r in R.runs) + sum(w["out"] for w in written),
           "usd_equiv": round(sum(r.get("usd_equiv") or 0 for r in R.runs) + sum(w.get("usd_equiv") or 0 for w in written), 2),
           "minutes": round((time.time() - t0) / 60, 1)}
    nights = json.load(open(f"{R.DATA}/nights.json")) if os.path.exists(f"{R.DATA}/nights.json") else []
    R.write_atomic(f"{R.DATA}/nights.json", json.dumps(nights + [rec], indent=1))
    old = json.load(open(f"{R.DATA}/runs.json")) if os.path.exists(f"{R.DATA}/runs.json") else []
    R.write_atomic(f"{R.DATA}/runs.json", json.dumps(old + [{**r, "night": label} for r in R.runs], indent=1))
    print("done", json.dumps(rec), flush=True)


if __name__ == "__main__":
    main()
