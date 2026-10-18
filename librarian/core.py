"""The librarian's passes, shared by nightly.py and weekly.py. Each night runs three:

  extract  the night's sources + the page index      -> candidate claims (with page, section, kind, ref)
  route    candidates that propose a new page        -> mapped to an existing page or a new page is made
  apply    each touched page + its candidates        -> edit ops on the page's claims (add, confirm, supersede, ...)

Agents' notes (from the hub's knowledge_note) join the apply pass as candidates of their own page; his fixes are
filed without a model (fold_fixes). The store is ROOT (data/pages.json, data/claims/<person>.jsonl, sources.json);
the hub reads it and lays the notes and fixes it hasn't seen filed yet over it (cos hub/knowledge.py).
Model: Sonnet 5.5 at medium effort on his Claude plan (claude-sandesh), no tools, no MCP servers.
"""
import json, os, re, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

# launchd starts the job with a bare PATH: claude-sandesh and fleet-secret live in ~/.local/bin, docker in Homebrew
os.environ["PATH"] = os.pathsep.join([os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin",
                                      "/usr/bin", "/bin", os.environ.get("PATH", "")])
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("KNOWLEDGE_ROOT") or os.path.expanduser("~/Documents/personal/ai-memory/knowledge/personal/wiki")
DATA = f"{ROOT}/data"
WORK = os.environ.get("LIBRARIAN_WORK") or "/tmp/librarian"   # the night's filtered input and the model's answers
PERSON = "sandesh"
AUTHOR = "librarian@aibo-mac"
CLAUDE = os.environ.get("LIBRARIAN_CLAUDE") or "claude-sandesh"   # his plan, never an API key
RULES = open(f"{HERE}/LIBRARIAN.md").read()
SOURCES = json.load(open(f"{ROOT}/sources.json")) if os.path.exists(f"{ROOT}/sources.json") else {}
FOLDED = {}   # note or fix id -> the claim it became ("dropped" if the librarian dropped it), for data/folded.json
SECTIONS = ("now", "decisions", "lessons", "open", "history")
PREFIX = re.compile(r"^(person|area|project|system|machine)/[a-z0-9][a-z0-9-]*$|^~[a-z]+/[a-z0-9][a-z0-9-]*$")
lock = threading.Lock()

SKELETON = [
    ("person/sandesh", "Sandesh", "Who he is, how he works, and his standing rules and preferences. The root page."),
    ("area/agents", "His agents", "How his AI agents work for him: models, approvals, groups and leads, the Chief."),
    ("project/cos", "cos (Chief of Staff hub)", "The hub where he runs his agents: board, Chief, groups, cards, apps."),
    ("system/memory", "Shared memory (Graphiti)", "The memory MCP: temporal knowledge graph, episodes, nightly export."),
    ("system/crew", "Crew messaging", "Agent-to-agent and agent-to-Chief messages, reports and asks."),
    ("system/cptr", "cptr", "The chat app agents run in; his fork and its patches."),
    ("system/agent-tools", "agent-tools", "Shared agent infrastructure: registry, MCP servers, skills, ntfy, artifacts."),
    ("system/secrets", "Secrets and logins", "keys.env per machine, 1Password, how agents sign in."),
    ("system/backups", "Backups", "What is backed up, where, and what he declined."),
    ("system/network-access", "Network and access", "Pangolin, SSO, SSH, DNS and how family reach apps."),
    ("machine/aibo-mac", "aibo-mac", "His Mac: personal apps (cos, the Chief, LifeOS)."),
    ("machine/aibo-linux", "aibo-linux", "The Linux server: agent infrastructure and home services."),
    ("area/home", "Home", "Home services: cameras, photos, files, media."),
    ("~sandesh/money", "Money", "His finances: accounts, routines, Splitwise, expense claims. Private."),
    ("~sandesh/family", "Family", "Family members and how they use his systems. Private."),
]

runs = []


# ---------------------------------------------------------------- state
def load():
    os.makedirs(f"{DATA}/claims", exist_ok=True)
    pages = json.load(open(f"{DATA}/pages.json")) if os.path.exists(f"{DATA}/pages.json") else {}
    claims = {}
    p = f"{DATA}/claims/{PERSON}.jsonl"
    if os.path.exists(p):
        for l in open(p):
            c = json.loads(l)
            claims[c["id"]] = c
    return pages, claims


def write_atomic(path, text):
    """The hub reads the store while the librarian writes it: a reader sees the old file or the new one, never half."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def removed_ids():
    """Facts taken out for good in cos (knowledge_remove): never written back, even if loaded before the removal."""
    out = set()
    if os.path.exists(f"{DATA}/removed.jsonl"):
        for x in open(f"{DATA}/removed.jsonl"):
            try:
                out.add(json.loads(x)["id"])
            except (ValueError, KeyError):
                pass
    return out


def save(pages, claims):
    for cid in removed_ids():
        claims.pop(cid, None)
    os.makedirs(f"{DATA}/claims", exist_ok=True)
    write_atomic(f"{DATA}/pages.json", json.dumps(pages, indent=1, ensure_ascii=False))
    order = sorted(claims.values(), key=lambda c: (not c["id"].split(":")[1].isdigit(), c["id"]))
    write_atomic(f"{DATA}/claims/{PERSON}.jsonl", "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in order))
    write_atomic(f"{ROOT}/sources.json", json.dumps(SOURCES, indent=0, ensure_ascii=False))
    if FOLDED:
        old = json.load(open(f"{DATA}/folded.json")) if os.path.exists(f"{DATA}/folded.json") else {}
        write_atomic(f"{DATA}/folded.json", json.dumps({**old, **FOLDED}, indent=0))


def new_id(claims):
    n = max([int(i.split(":")[1]) for i in claims if i.split(":")[1].isdigit()] or [0]) + 1
    return f"{PERSON}:{n:04d}"


def pinned(c):
    """His own correction from cos (a Fix): only his own newer words may replace it."""
    return bool(c) and (str(c.get("author", "")).endswith("@cos") or bool(c.get("pinned")))


def make_page(pages, pid, title, summary, night):
    pages[pid] = {"id": pid, "title": title, "summary": summary, "owner": PERSON,
                  "space": f"private/{pid[1:].split('/')[0]}" if pid.startswith("~") else "shared",
                  "created": night, "created_by": AUTHOR}


# ---------------------------------------------------------------- model
def claude(prompt, label, tries=2):
    cmd = [CLAUDE, "-p", "--model", "sonnet", "--effort", "medium", "--tools", "",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence",
           "--output-format", "json", "--system-prompt", RULES]
    for t in range(tries):
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd="/tmp", timeout=1800)
        try:
            j = json.loads(r.stdout)
        except Exception:
            j = {"is_error": True, "result": r.stdout[:300] + r.stderr[:300]}
        if not j.get("is_error"):
            break
        print(f"  {label}: error {j.get('result', '')[:200]} (try {t + 1})", flush=True)
        time.sleep(20)
    else:
        raise SystemExit(f"{label} failed")
    os.makedirs(f"{WORK}/out", exist_ok=True)
    open(f"{WORK}/out/{label.replace('/', '_').replace(' ', '_')}.txt", "w").write(j.get("result", ""))
    u = j.get("usage", {})
    rec = {"step": label, "models": sorted(j.get("modelUsage", {})),
           "in": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0),
           "out": u.get("output_tokens", 0), "usd_equiv": j.get("total_cost_usd"),
           "seconds": round(j.get("duration_ms", 0) / 1000)}
    with lock:
        runs.append(rec)
    print(f"  {label}: in {rec['in']:,} out {rec['out']:,} {rec['seconds']}s", flush=True)
    return j.get("result", "")


def parse_json(text):
    text = re.sub(r"^```\w*\n|\n```$", "", text.strip())
    a, b = text.find("{"), text.rfind("}")
    return json.loads(text[a:b + 1])


def parse_jsonl(text):
    out = []
    for l in text.splitlines():
        l = l.strip().rstrip(",")
        if l.startswith("{"):
            try:
                out.append(json.loads(l))
            except Exception:
                pass
    return out


# ---------------------------------------------------------------- views for the model
def index_text(pages, claims):
    cnt = {}
    for c in claims.values():
        if c["status"] == "current":
            cnt[c["page"]] = cnt.get(c["page"], 0) + 1
    return "\n".join(f"{p['id']} · {p['title']} · {p['summary'][:110]} · {cnt.get(p['id'], 0)} claims"
                     for p in sorted(pages.values(), key=lambda p: p["id"]))


def page_text(pid, pages, claims):
    p = pages[pid]
    lines = [f"PAGE {pid} · {p['title']} · {p['space']} · owner {p['owner']}", f"summary: {p['summary']}"]
    cs = [c for c in claims.values() if c["page"] == pid]
    for sec in SECTIONS[:-1]:
        cur = [c for c in cs if c["section"] == sec and c["status"] == "current"]
        if cur:
            lines.append(f"## {sec}")
            for c in sorted(cur, key=lambda c: c["date"]):
                extra = (f" · why: {c['why']}" if c.get("why") else "") + \
                        (f" · turned down: {c['rejected']}" if c.get("rejected") else "")
                pin = " · HIS CORRECTION, pinned: keep it" if pinned(c) else ""
                lines.append(f"- [{c['id']}] {c['date']} · {c['kind']}{pin} · {c['text']}{extra}")
    hist = sorted([c for c in cs if c["status"] != "current" or c["section"] == "history"],
                  key=lambda c: c["date"])[-8:]
    if hist:
        lines.append("## history (latest 8)")
        lines += [f"- [{c['id']}] {c['date']} · {c['status']} · {c['text'][:160]}" for c in hist]
    return "\n".join(lines)


# ---------------------------------------------------------------- passes
EXTRACT = """Tonight's sources for {person} are below, one block per source, each headed `=== Sxxxx · type · agent · group ·
machine · date · chat`. The wiki's current page index comes first.

Write candidate claims for the wiki, one JSON object per line (JSONL), nothing else:
{{"page": "<existing page id>", "section": "now|decisions|lessons|open|history", "text": "...", "kind": "said|relayed|observed|inferred", "ref": "S0123", "date": "YYYY-MM-DD", "why": "", "rejected": ""}}

- Use an existing page id when one fits. Otherwise use "new:<prefix>/<slug>|<Title>", for example
  "new:system/frigate|Frigate (cameras)". A new page needs at least two claims of its own.
- Facts about how he works, what he likes and his rules for all agents go on person/sandesh.
- One page, one thing. When a broad page (like project/cos) already has a sub-page that fits, use it. When a topic
  keeps coming up with nothing of its own, propose a sub-page such as "new:project/cos-board|cos board".
- Keep only what will still matter next week: decisions and their reasons, how things are set up, lessons, open
  questions and promises, and changes that went live. Skip routine progress and anything about the agents' own
  tooling chatter.
- It's fine to write nothing for a block. Aim for quality: a busy night gives about 60–120 lines.

# Page index
{index}

# Sources
{chunk}"""

ROUTE = """Some candidate claims propose new pages. For each proposal, decide whether to use an existing page or make a
new one. Prefer existing pages, and merge proposals that mean the same thing. New ids must match
`person|area|project|system|machine/<slug>` or `~sandesh/<slug>` (private: money, family, health, personal admin).

Answer with JSON only: {{"<proposal>": {{"use": "<existing id>"}} or {{"create": "<new id>", "title": "...",
"summary": "<one line>"}}, ...}}

# Page index
{index}

# Proposals (with sample claims)
{props}"""

APPLY = """You are editing wiki pages. For each page below you get its current claims (with ids) and tonight's
candidate claims (c1, c2, …). Decide what each candidate does to the page.

Answer with JSON only, in this shape:
{{"<page id>": {{"summary": "<new one-line summary, only if it should change>", "ops": [ ... ]}}, ...}}

Ops (use the candidate's number as "c"):
- {{"op": "add", "c": 3, "section": "now", "text": "<optional cleaner wording>"}}: a new claim.
- {{"op": "confirm", "id": "sandesh:0042", "c": 3}}: it repeats an existing claim; add its source.
- {{"op": "supersede", "id": "sandesh:0042", "c": 5, "section": "now", "text": "<optional>"}}: a newer claim
  replaces an older one. The old one moves to history.
- {{"op": "resolve", "id": "sandesh:0050", "c": 6, "text": "<what settled it>"}}: an open item is now settled.
- {{"op": "merge", "ids": ["sandesh:0011", "sandesh:0012"], "section": "now", "text": "<merged claim>"}}: two or
  more existing claims say nearly the same thing; join them, keeping every source.
- {{"op": "drop", "c": 7, "reason": "<progress | duplicate | not durable | wrong page | secret>"}}.

Every candidate must get exactly one op. Keep decisions as decisions (needs said or relayed), and put "tried, didn't
work" in lessons. Write in plain, short sentences; keep [[page]] links.

{pages}"""

TIDY = """This page has grown too long. Consolidate it without losing anything true: merge claims that overlap,
supersede claims that a newer one already replaces, and move items that are now only history to history (supersede
them with a short history claim). Keep the now section under about 25 claims. Use only "merge" and "supersede" ops
(for supersede here, give "text" and "section" and no "c"). Also give a fresh one-line "summary".

Answer with JSON only: {{"{pid}": {{"summary": "...", "ops": [ ... ]}}}}

{page}"""


def extract(night, pages, claims, text=None):
    text = open(f"{WORK}/in/{night}.txt").read() if text is None else text
    if not text.strip():
        return []
    blocks = text.split("\n\n=== ")
    chunks, cur = [], ""
    for b in blocks:
        if len(cur) + len(b) > 120_000 and cur:
            chunks.append(cur)
            cur = ""
        cur += ("\n\n=== " if cur else "") + b.lstrip("= ")
    if cur:
        chunks.append(cur)
    idx = index_text(pages, claims)
    with ThreadPoolExecutor(4) as ex:
        outs = list(ex.map(lambda ic: claude(EXTRACT.format(person=PERSON, index=idx, chunk="=== " + ic[1]),
                                             f"{night} extract {ic[0] + 1}/{len(chunks)}"), enumerate(chunks)))
    cands = [c for o in outs for c in parse_jsonl(o)]
    return [c for c in cands if c.get("ref") in SOURCES and c.get("text") and c.get("section") in SECTIONS]


def route(night, cands, pages, claims):
    props = {}
    for c in cands:
        if c["page"].startswith("new:"):
            props.setdefault(c["page"], []).append(c["text"][:140])
    bad = [c for c in cands if not c["page"].startswith("new:") and c["page"] not in pages]
    for c in bad:   # unknown id without "new:": treat as a proposal
        c["page"] = f"new:{c['page']}|{c['page'].split('/')[-1]}"
        props.setdefault(c["page"], []).append(c["text"][:140])
    if not props:
        return cands
    ptxt = "\n".join(f"{k} ({len(v)} claims): " + " | ".join(v[:3]) for k, v in props.items())
    m = parse_json(claude(ROUTE.format(index=index_text(pages, claims), props=ptxt), f"{night} route"))
    for k, v in m.items():
        if "create" in v and PREFIX.match(v["create"]) and v["create"] not in pages:
            make_page(pages, v["create"], v.get("title", v["create"]), v.get("summary", ""), night)
    out = []
    for c in cands:
        if c["page"].startswith("new:"):
            v = m.get(c["page"], {})
            tgt = v.get("use") or v.get("create")
            if tgt not in pages:
                continue
            c["page"] = tgt
        out.append(c)
    return out


def new_claim(claims, c, page, section, text, night):
    cid = c.get("note") if c.get("note") and c["note"] not in claims else new_id(claims)
    src = SOURCES.get(c["ref"], {})
    claims[cid] = {"id": cid, "page": page, "section": section, "text": text or c["text"], "kind": c.get("kind", "observed"),
                   "person": PERSON, "date": c.get("date") or src.get("date", ""), "why": c.get("why", ""),
                   "rejected": c.get("rejected", ""), "refs": [c["ref"]], "status": "current", "night": night,
                   "author": c.get("author") or AUTHOR}
    for r in c.get("replaces") or []:   # a note that corrects a fact
        if r in claims and r != cid and claims[r]["status"] == "current" and not pinned(claims[r]):
            claims[r].update(status="superseded", superseded_by=cid, superseded_on=night)
            claims[cid]["replaces"] = sorted(set(claims[cid].get("replaces", []) + [r]))
    if c.get("note"):
        FOLDED[c["note"]] = cid
    return cid


def apply_ops(pid, res, cands, pages, claims, night, stats):
    if res.get("summary"):
        pages[pid]["summary"] = res["summary"]
    for op in res.get("ops", []):
        kind = op.get("op")
        c = cands[op["c"] - 1] if isinstance(op.get("c"), int) and 0 < op["c"] <= len(cands) else None
        old = claims.get(op.get("id", ""))
        if old and not old["id"].startswith(PERSON + ":"):
            kind = "conflict"     # another person's claim: never edit it
        sec = op.get("section") if op.get("section") in SECTIONS else (c or {}).get("section", "now")
        if kind in ("supersede", "resolve") and pinned(old) and (c or {}).get("kind") != "said":
            kind = "add" if c else "keep"   # his correction stays; an agent's newer line sits beside it
        if kind == "add" and c:
            new_claim(claims, c, pid, sec, op.get("text"), night)
        elif kind == "confirm" and c and old:
            old["refs"] = sorted(set(old["refs"] + [c["ref"]]))
            if c.get("note"):
                FOLDED[c["note"]] = old["id"]
        elif kind == "supersede" and old:
            if c:
                nid = new_claim(claims, c, pid, sec, op.get("text"), night)
            else:
                nid = new_id(claims)
                claims[nid] = {**old, "id": nid, "section": sec, "text": op.get("text") or old["text"],
                               "status": "current", "night": night, "replaces": [old["id"]]}
            claims[nid]["replaces"] = sorted(set(claims[nid].get("replaces", []) + [old["id"]]))
            old.update(status="superseded", superseded_by=nid, superseded_on=night)
        elif kind == "resolve" and old:
            old.update(status="resolved", superseded_on=night)
            if c:
                nid = new_claim(claims, c, pid, "history", op.get("text"), night)
                old["superseded_by"] = nid
        elif kind == "merge":
            olds = [claims[i] for i in op.get("ids", []) if i in claims and claims[i]["status"] == "current"
                    and not pinned(claims[i])]
            if len(olds) >= 2 and op.get("text"):
                nid = new_id(claims)
                first = min(olds, key=lambda o: o["date"])
                claims[nid] = {**first, "id": nid, "section": sec, "text": op["text"], "status": "current",
                               "refs": sorted({r for o in olds for r in o["refs"]}), "night": night,
                               "date": max(o["date"] for o in olds), "replaces": [o["id"] for o in olds],
                               "kind": "said" if any(o["kind"] == "said" for o in olds) else first["kind"]}
                for o in olds:
                    o.update(status="superseded", superseded_by=nid, superseded_on=night)
        elif kind == "conflict" and c:
            nid = new_claim(claims, c, pid, sec, op.get("text"), night)
            claims[nid]["disputes"] = op.get("id")
        elif kind == "drop" and c and c.get("note"):
            FOLDED[c["note"]] = "dropped"
        stats[kind] = stats.get(kind, 0) + 1


def apply(night, cands, pages, claims):
    by = {}
    for c in cands:
        by.setdefault(c["page"], []).append(c)
    batches, cur, size = [], [], 0
    for pid in sorted(by, key=lambda p: -len(by[p])):
        t = len(page_text(pid, pages, claims)) + sum(len(c["text"]) + 60 for c in by[pid])
        if cur and (size + t > 45_000 or len(cur) >= 6):
            batches.append(cur)
            cur, size = [], 0
        cur.append(pid)
        size += t
    if cur:
        batches.append(cur)

    def body(batch):
        out = []
        for pid in batch:
            cl = "\n".join(f"c{i + 1}. [{c['section']}] {c['date']} · {c['kind']} · {c['text']}"
                           + (f" · why: {c['why']}" if c.get("why") else "")
                           + (f" · turned down: {c['rejected']}" if c.get("rejected") else "")
                           for i, c in enumerate(by[pid]))
            out.append(f"{page_text(pid, pages, claims)}\n\n### candidates for {pid}\n{cl}")
        return "\n\n---\n\n".join(out)

    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda ib: (ib[1], claude(APPLY.format(pages=body(ib[1])),
                                                           f"{night} apply {ib[0] + 1}/{len(batches)}")),
                              enumerate(batches)))
    stats = {}
    for batch, out in results:
        try:
            res = parse_json(out)
        except Exception:
            print(f"  {night}: could not parse apply output for {batch}", flush=True)
            continue
        for pid in batch:
            if pid in res:
                apply_ops(pid, res[pid], by[pid], pages, claims, night, stats)
    for c in cands:   # a note the model gave no op: it's his words (or relayed), so it goes in as it is
        if c.get("note") and c["note"] not in FOLDED:
            new_claim(claims, c, c["page"], c["section"], None, night)
            stats["add"] = stats.get("add", 0) + 1
    return stats


def tidy(pages, claims, limit=32):
    big = [p for p in pages if sum(1 for c in claims.values() if c["page"] == p and c["status"] == "current"
                                   and c["section"] != "history") > limit]
    def one(pid):
        return pid, claude(TIDY.format(pid=pid, page=page_text(pid, pages, claims)), f"tidy {pid}")
    with ThreadPoolExecutor(4) as ex:
        outs = list(ex.map(one, big))
    stats = {}
    for pid, out in outs:
        try:
            res = parse_json(out).get(pid, {})
        except Exception:
            continue
        apply_ops(pid, res, [], pages, claims, "tidy", stats)
    return big, stats
