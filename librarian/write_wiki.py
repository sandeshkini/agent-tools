"""Librarian, wiki pass (weekly.py): rewrite pages as prose from their facts.

Writes, in the store (the hub reads them; it never writes them):
  data/wiki/<file>.md      one page each, an article: a :::box fact box, the lead, then sections chosen for the
                           subject; every sentence cites its facts as [^sandesh:0123]. The record (decisions,
                           lessons, open, history) isn't written here: cos draws it from the facts
  data/wiki/index.json     page id -> {file, written, facts_cited, model}
The tree (data/tree.json) and the typed links (data/links.json) are kept as they are; a page the tree doesn't place
shows under "Other" in cos until the tree is edited.

Facts are read through the hub's reader, so his fixes are laid over: a fact he fixed is his words and the page must
keep its wording (pinned). Model: Sonnet 5.5 at medium on his plan (claude-sandesh), no tools, no MCP servers.

    python3 -I write_wiki.py              # tree + links + every page
    python3 -I write_wiki.py project/cos  # just these pages
"""
import datetime, json, os, re, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core as R
sys.path.insert(0, os.path.expanduser("~/Documents/personal/apps/cos/hub"))
import knowledge as K  # the hub's reader: fixes and notes overlaid

KN = R.ROOT
DATA = f"{KN}/data"
WIKI = f"{DATA}/wiki"

RULES = """You are the librarian of Sandesh's wiki: pages about the things in his life and work, written like a good
Wikipedia or Notion page for a reader years from now with none of today's context (a new agent, a colleague, or
Sandesh himself later). You write one page from the facts given. Each fact has an id, a date, whose word it is
(said = his own words, relayed = his words passed on, observed = an agent saw it, inferred = an agent's view) and a
status (current, or superseded = no longer true, kept as history). A fact whose author is sandesh@cos is his own
correction: it is pinned, so keep its wording and never contradict it.

Write it as an encyclopedia article: calm, plain prose in short paragraphs, using a short list only where a list is the
natural shape (steps, a set of rules). Explain what it is, how it works and why it is this way, and define anything
a newcomer wouldn't know. Never pad and never invent: every sentence rests on the facts. End every sentence with the
facts it rests on, as [^sandesh:0123] (one or more). Never cite a superseded fact for something true now. Link other
pages where they come up, as [[page id|name]]. No secrets, numbers or other people's private details. Leave out
trivia and step-by-step progress.

The shape, in this order:
1. A fact box: the 4-6 things a reader would look up first (what it is, where it runs, who uses it, the rules
   that bound it), one per line, each as "Label: value [^id]" with a label of one or two words:
   :::box <what kind of subject it is, one or two words: Project, System, Person, Area>
   Label: value [^sandesh:0123]
   :::
2. The lead: one or two paragraphs with no heading, saying what this is and why it matters, so a reader who stops
   there knows the essentials.
3. Two to five sections, each a "## " heading you choose for this subject (e.g. "The app", "How it runs", "How he
   works"; never "In short", "Overview" or "Introduction"). Put each decision in the section it shaped, with its
   reason and what was turned down; put a lesson where it applies.
Don't write sections for decisions, lessons, open items or history: cos shows those under the article as the
record, drawn straight from the facts. Output only the Markdown (no title, no frontmatter). Keep it under 1,000
words; a page with few facts is short, and may have no sections at all."""


def fname(pid):
    return pid.replace("~", "_").replace("/", "__") + ".md"


def facts_for(st, pid, tree_parent, subs):
    cs = [c for c in st.claims.values() if c["page"] == pid]
    if subs:   # a hub also draws on its sub-topics' decisions and lessons in his words
        cs += [c for c in st.claims.values() if c["page"] in subs and c.get("kind") in ("said", "relayed")
               and c.get("section") in ("decisions", "lessons") and c.get("status") == "current"]
    return sorted(cs, key=lambda c: c.get("date") or "")[-170:]


def line(c):
    x = (f" Why: {c['why']}." if c.get("why") else "") + (f" Turned down: {c['rejected']}." if c.get("rejected") else "")
    who = " · his correction (pinned)" if str(c.get("author", "")).endswith("@cos") else ""
    return f"- [{c['id']}] {c['date']} · {c['kind']} · {c['status']}{who} · on {c['page']} · {K.plain(c['text'])}{x}"


def write(st, pid, parent, sections, titles):
    subs = [p for s in sections for p in s["pages"]]
    fs = facts_for(st, pid, parent, subs)
    ctx = []
    if parent:
        ctx.append(f"This page is part of [[{parent}|{titles.get(parent, parent)}]].")
    if sections:
        ctx.append("This page is a hub. Walk through its sub-topics in the sections, one short paragraph per group, "
                   "each linking its pages: " + "; ".join(f"{s['title']}: " + ", ".join(f"[[{p}|{titles.get(p, p)}]]" for p in s["pages"]) for s in sections))
    title = titles.get(pid) or st.pages[pid]["title"]
    prompt = (f'Write the page "{title}" ({pid}).\n' + "\n".join(ctx) + "\n\nPages you may link: " +
              ", ".join(f"[[{k}|{titles.get(k, v['title'])}]]" for k, v in st.pages.items()) + "\n\n# Facts\n" + "\n".join(map(line, fs)))
    cmd = [R.CLAUDE, "-p", "--model", "sonnet", "--effort", "medium", "--tools", "", "--strict-mcp-config", "--mcp-config",
           '{"mcpServers":{}}', "--no-session-persistence", "--output-format", "json", "--system-prompt", RULES]
    for attempt in range(2):
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd="/tmp", timeout=1200)
        try:
            j = json.loads(r.stdout)
        except Exception:
            j = {"is_error": True, "result": r.stdout[:200]}
        if not j.get("is_error"):
            break
    else:
        raise SystemExit(f"{pid}: {j.get('result')}")
    body = j["result"].strip()
    known = set(st.claims)
    cited = set(re.findall(r"\[\^(sandesh:[cn]?\d+)\]", body))
    body = re.sub(r"\[\^(sandesh:[cn]?\d+)\]", lambda m: m.group(0) if m.group(1) in known else "", body)   # drop unknown citations
    os.makedirs(WIKI, exist_ok=True)
    R.write_atomic(f"{WIKI}/{fname(pid)}", body + "\n")
    u = j["usage"]
    rec = {"file": fname(pid), "written": datetime.date.today().isoformat(), "facts_given": len(fs),
           "facts_cited": len(cited & known), "dropped_citations": len(cited - known), "model": "claude-sonnet-5-5",
           "shape": "article", "in": u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"], "out": u["output_tokens"],
           "usd_equiv": j.get("total_cost_usd") or 0}
    print(pid, {k: rec[k] for k in ("facts_given", "facts_cited", "dropped_citations", "in", "out")}, flush=True)
    return pid, rec


def changed(st, idx):
    """Pages whose facts changed since the page was last written (a new, replaced or fixed fact)."""
    out = []
    for pid in st.pages:
        w = (idx.get(pid) or {}).get("written") or ""
        cs = [c for c in st.claims.values() if c["page"] == pid]
        last = max([c.get("date") or "" for c in cs] + [str(c.get("superseded_on") or "")[1:] for c in cs
                                                       if str(c.get("superseded_on") or "").startswith("D")], default="")
        if not w or last >= w:
            out.append(pid)
    return out


def main(pids=None, everything=False):
    """Rewrite the pages named, else every page whose facts changed since it was written (all with everything)."""
    st = K.Store(KN, R.PERSON).load()
    tree = st.tree or {"hubs": [], "titles": {}}
    titles = tree.get("titles") or {}
    parent = {p: h["id"] for h in tree.get("hubs") or [] for s in h.get("sections") or [] for p in s.get("pages") or []}
    secs = {h["id"]: h.get("sections") or [] for h in tree.get("hubs") or []}
    idx = json.load(open(f"{WIKI}/index.json")) if os.path.exists(f"{WIKI}/index.json") else {}
    todo = [p for p in (pids or []) if p in st.pages] or (sorted(st.pages) if everything else changed(st, idx))
    with ThreadPoolExecutor(4) as ex:
        for pid, rec in ex.map(lambda p: write(st, p, parent.get(p) or st.pages[p].get("parent"), secs.get(p, []), titles), todo):
            idx[pid] = rec
    R.write_atomic(f"{WIKI}/index.json", json.dumps(idx, indent=1))
    return [idx[p] for p in todo]


if __name__ == "__main__":
    main(sys.argv[1:], "--all" in sys.argv)
