"""Librarian, link pass: one small model call gives each page a few aliases (names that, in a claim, mean that page);
render.py then links the first mention of an alias in each claim to the page. Stored as pages[id]["aliases"].
Explicit [[page]] links written by the model still work alongside.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import core as R

PROMPT = """For each wiki page below, give 1 to 6 aliases: words or short phrases that, when they appear in a claim on
another page, clearly mean this page. Use the names people actually write (for example "Frigate", "Graphiti", "the
board", "Money group", "Splitwise"). Avoid words that are too common to be unambiguous ("agents", "home", "money",
"memory" alone, "group"). A sub-page's aliases must be more specific than its parent's.

Answer with JSON only: {{"<page id>": ["alias", ...], ...}}

{index}"""


def main():  # weekly.py runs this
    pages, claims = R.load()
    out = R.parse_json(R.claude(PROMPT.format(index=R.index_text(pages, claims)), "link aliases"))
    for pid, al in out.items():
        if pid in pages:
            pages[pid]["aliases"] = [a for a in al if isinstance(a, str) and len(a) >= 3][:6]
    R.save(pages, claims)


if __name__ == "__main__":
    main()
