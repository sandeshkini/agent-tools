"""The librarian's nightly run: what happened since the last run becomes facts in the wiki.

  1. collect (no model): the chat turns, crew messages, cards he answered, ledger lines and memory episodes that are
     new since the last run, filtered and scrubbed into source blocks (each gets a ref in sources.json);
  2. file his fixes from cos (no model) and the agents' notes (knowledge_note) that the hub holds;
  3. extract, route, apply (core.py) on his plan, then a leak check, then save. The store is in ai-memory, which
     sync-repos commits and pushes every 10 minutes.

What it has read is kept in data/librarian.json (the last run's time, how many turns of each chat, which episodes
and ledger lines), so a chat that goes on for days is read once, a turn at a time.

  python3 -I nightly.py                    # since the last run, until now
  python3 -I nightly.py --collect-only     # write the night's input to $LIBRARIAN_WORK/in and stop
  python3 -I nightly.py --until 2026-10-18T03:00

Reads aibo-linux over SSH (remote_collect.py, scrubbed there with its own keys.env). The work Mac's chats
(/Users/artos…) are left out: they belong to the work wiki.
"""
import glob, hashlib, json, os, re, sqlite3, subprocess, sys, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core as R
from scrub import scrub, leaks, HITS

P = os.path.expanduser("~/Documents/personal")
STATE = f"{R.DATA}/librarian.json"
SKIP_DIR = re.compile(r"^/Users/artos|^/tmp|^/private/tmp|throwaway|p20smoke|^/$")
KEY = re.compile(r"decid|because|root cause|turns out|didn't work|doesn't work|failed|instead|reverted|rolled back|"
                 r"already|the reason|lesson|gotcha|never|always|prefer", re.I)
TURN = re.compile(r"\n(?=\*\*(?:You|Claude|Assistant):\*\*)")
blocks = []


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def load_state():
    return json.load(open(STATE)) if os.path.exists(STATE) else {"last": "", "sessions": {}, "episodes": [], "ledger": []}


def add(typ, body, **prov):
    if not body.strip():
        return None
    n = max([int(k[1:]) for k in R.SOURCES if re.fullmatch(r"S\d+", k)] or [0]) + 1
    ref = f"S{n:04d}"
    prov.update(type=typ, night=NIGHT)
    R.SOURCES[ref] = prov
    head = " · ".join(str(x) for x in [typ, prov.get("agent", ""), prov.get("group", ""), prov.get("machine", ""),
                                         prov.get("date", ""), prov.get("chat", "")] if x)
    blocks.append(f"=== {ref} · {head}\n{scrub(body).strip()}")
    return ref


# ---------------------------------------------------------------- where each chat came from
def hub_rows(lo_ms):
    """The hub's agent names, and the cards answered since lo (read-only, inside the hub's container)."""
    code = f"""
import sqlite3, json
c = sqlite3.connect('file:/data/cos.db?mode=ro', uri=True); c.row_factory = sqlite3.Row
print(json.dumps({{'agents': [dict(r) for r in c.execute('select thread_id, name, topic, lead from agents')],
  'cards': [dict(r) for r in c.execute('select id, node, chat_id, title, question, headline, options, reply, pick, created_at, replied_at from cards where (reply is not null and reply != "") or (pick is not null and pick != "")')]}}))
"""
    r = subprocess.run(["docker", "exec", "cos-hub-1", "python3", "-c", code], capture_output=True, text=True, timeout=60)
    if r.returncode:
        log("cos.db not readable (hub down?): no agent names or cards tonight:", r.stderr[-200:])
        return {"agents": [], "cards": []}
    return json.loads(r.stdout)


def agent_for(agents, machine, chat_id, title, d):
    a = agents.get(f"{machine}:{chat_id}") or {}
    tail = d.replace(os.path.expanduser("~"), "~").rstrip("/").split("/")[-1] or "~"
    title = (title or "").split("\n")[0].strip()
    title = "" if title.startswith(("⟦", "[")) else title[:60]
    name = a.get("name") or title or f"chat in {tail}"
    group = (a.get("topic") or "").lower() if (a.get("topic") or "").lower() in ("cos", "money", "chief", "lifeos") else ""
    if "/var/chief" in d:
        name, group = "Chief", "chief"
    m = re.search(r"/var/groups/([^/]+)", d)
    if m:
        name, group = f"{m.group(1)} lead", m.group(1)
    return name, group


def ms(iso):
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


# ---------------------------------------------------------------- collect
def collect(st, lo, hi):
    lo_ms, hi_ms = ms(lo), ms(hi)
    chatmap = {}
    try:
        cp = sqlite3.connect(f"file:{os.path.expanduser('~/.cptr/app.db')}?mode=ro", uri=True)
        for cid, title, meta in cp.execute("select id, title, meta from chats"):
            for s in (json.loads(meta or "{}").get("agent_sessions") or {}).values():
                if s.get("session_id"):
                    chatmap[s["session_id"][:8]] = {"chat_id": cid, "title": title, "machine": "aibo-mac"}
    except Exception as e:
        log("cptr map:", e)
    # its own key and no agent: under launchd the 1Password SSH agent would prompt or hang
    remote = subprocess.run(f"cat scrub.py remote_collect.py | ssh -o BatchMode=yes -o IdentityAgent=none -o ConnectTimeout=20 -i ~/.ssh/id_ed25519 aibo-linux 'python3 - {lo} {hi} --since-mtime'",
                            shell=True, capture_output=True, text=True, cwd=R.HERE, timeout=600)
    if remote.returncode:
        raise SystemExit(f"aibo-linux collect failed: {remote.stderr[-400:]}")
    linux = json.loads(remote.stdout)
    for k, v in linux["chats"].items():
        chatmap.setdefault(k, v)
    hub = hub_rows(lo_ms)
    agents = {a["thread_id"]: a for a in hub["agents"]}

    # chats: the turns since the last run, from every session written since then (both machines)
    files = {}
    slack = time.mktime(datetime.fromisoformat(lo).timetuple()) - 3600
    for f in glob.glob(f"{P}/ai-memory/sessions/*.md"):
        if os.path.getmtime(f) >= slack:
            files[os.path.basename(f)] = None
    files.update(linux["sessions"])
    n_chats = 0
    for base in sorted(files):
        m = re.search(r"-(\d{4})(\d{2})(\d{2})-\d{4}\.md$", base)
        if not m:
            continue
        s = files[base] if files[base] is not None else open(f"{P}/ai-memory/sessions/{base}", errors="replace").read()
        dm = re.search(r"\*\*Dir:\*\* (\S+)", s)
        d = dm.group(1) if dm else ""
        if SKIP_DIR.search(d) or (d.startswith("/home/") and files[base] is None):
            continue   # aibo-linux chats come only from the copy scrubbed there
        turns = TURN.split(s)[1:]
        seen = int(st["sessions"].get(base, 0))
        st["sessions"][base] = len(turns)
        new = turns[seen:]
        if not new:
            continue
        sid = base[:8]
        cm = chatmap.get(sid, {})
        machine = cm.get("machine") or ("aibo-linux" if d.startswith("/home/") else "aibo-mac")
        name, group = agent_for(agents, machine, cm.get("chat_id", ""), cm.get("title", ""), d)
        lead = name == "Chief" or name.endswith(" lead")
        you = [t.split(":**", 1)[1].strip() for t in new if t.startswith("**You:**")]
        me = [t.split(":**", 1)[1].strip() for t in new if t.startswith(("**Claude:**", "**Assistant:**"))]
        own = [y for y in you if not re.match(r"\s*(⟦|<|\[Image|\[SYSTEM|Caveat:)", y) and len(y) > 3]
        seg = []
        if seen == 0 and you and you[0].startswith("⟦"):
            seg.append(f"BRIEF (passed on): {you[0][:600]}")
        elif seen:
            seg.append("(this chat goes on from an earlier night)")
        seg += [f"PASSED ON: {y[:400]}" for y in you if y.startswith("⟦") and (seen or y is not you[0])][:6]
        seg += [f"HIM: {y[:600 if lead else 700]}" for y in own[:70 if lead else 25]]
        seg += [f"AGENT NOTE: {x[:280]}" for x in [x for x in me[:-1] if KEY.search(x)][-6:]]
        if me:
            seg.append(f"AGENT FINAL: {me[-1][:1000]}")
        if not own and not me:
            continue
        day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if not seen else hi[:10]
        add("chat", "\n".join(seg), agent=name, group=group, machine=machine, date=day,
            chat=cm.get("chat_id", sid)[:8], session=sid, spoke="his words (HIM lines) + agent")
        n_chats += 1

    # crew messages, one block per day and sender -> receiver
    crew = {}
    cut = {"report": 450, "ask": 450, "start": 300, "relay": 220}
    for mm in linux["messages"]:
        t = datetime.fromtimestamp(mm["ts"] / 1000)
        line = f"[{t:%H:%M}] {mm['kind']}: {mm['body'].replace(chr(10), ' ')[:cut[mm['kind']]]}"
        if mm["kind"] == "ask" and mm["reply"]:
            line += f"\n    answer: {mm['reply'].replace(chr(10), ' ')[:400]}"
        crew.setdefault((t.strftime("%Y-%m-%d"), mm["from"], mm["to"]), []).append(line)
    for (day, frm, to), lines in sorted(crew.items()):
        add("crew", "\n".join(lines), agent=f"{frm} → {to}", machine="aibo-linux (messaging)", date=day,
            spoke="his words" if frm == "Sandesh" else "agent")

    # cards he answered since the last run, one block per day and agent
    cards = {}
    for c in hub["cards"]:
        at = c.get("replied_at") or c["created_at"]
        at = at if at > 1e11 else at * 1000
        if not (lo_ms <= at < hi_ms):
            continue
        a = agents.get(f"{c['node']}:{c['chat_id']}") or {}
        key = (datetime.fromtimestamp(at / 1000).strftime("%Y-%m-%d"), a.get("name") or c.get("title") or "?",
               c["node"], (c["chat_id"] or "")[:8], a.get("topic") or "")
        cards.setdefault(key, []).append(
            f"Q: {(c.get('question') or c.get('headline') or c.get('title') or '')[:300]} · options: "
            f"{(c.get('options') or '')[:200]}\n    HIS ANSWER: {(c.get('reply') or c.get('pick'))[:300]}")
    for (day, name, node, chat, group), lines in sorted(cards.items()):
        add("card", "\n".join(lines), agent=name, group=group, machine=node, date=day, chat=chat,
            spoke="his words (HIS ANSWER)")

    # group ledgers: lines not read before
    seen_l = set(st["ledger"])
    for f in glob.glob(f"{P}/personal-agent/groups/*/ledger.md"):
        g = f.split("/groups/")[1].split("/")[0]
        byday = {}
        for line in open(f):
            m = re.match(r"(\d{4}-\d\d-\d\d) · (.*)", line)
            h = hashlib.sha1(line.strip().encode()).hexdigest()[:12]
            if m and h not in seen_l:
                seen_l.add(h)
                byday.setdefault(m.group(1), []).append(m.group(2).strip())
        for day, ls in sorted(byday.items()):
            add("ledger", "\n".join(ls), agent=f"{g} lead (ledger)", group=g, machine="aibo-mac", date=day,
                path=f.replace(P, "~/Documents/personal"), spoke="agent")
    st["ledger"] = sorted(seen_l)

    # memory episodes (Graphiti's export) not read before
    seen_e = set(st["episodes"])
    # (imports/: episodes taken straight from the graph, written after its last export)
    for f in sorted(glob.glob(f"{P}/ai-memory/knowledge/personal/episodes/*.md") + glob.glob(f"{R.ROOT}/imports/*.md")):
        for b in open(f).read().split("\n## ")[1:]:
            eid = re.search(r"\*\*id:\*\* `([0-9a-f-]+)`", b)
            if not eid or eid.group(1) in seen_e:
                continue
            seen_e.add(eid.group(1))
            title = b.split("\n", 1)[0].strip()
            rec = re.search(r"\*\*recorded:\*\* (\S+)", b)
            src = re.search(r"\*\*source:\*\* (.+)", b)
            body = re.sub(r"- \*\*(recorded|source|id):\*\*.*\n", "", b.split("\n", 1)[1]).strip()
            s = src.group(1) if src else ""
            add("memory episode", f"{title}\n{body}", agent=s.split(",")[0].split("@")[0].strip() or "memory",
                machine="aibo-linux (memory)", date=rec.group(1) if rec else "", chat=f"episode {eid.group(1)[:8]}",
                spoke="his words, recorded" if ("from Sandesh" in s or "direct" in s) else "agent")
    st["episodes"] = sorted(seen_e)
    return {"chats": n_chats, "crew": len(crew), "cards": len(cards), "linux_sessions": len(linux["sessions"])}


# ---------------------------------------------------------------- his fixes and the agents' notes
def jsonl(path):
    out = []
    if os.path.exists(path):
        for x in open(path):
            try:
                out.append(json.loads(x))
            except ValueError:
                pass
    return out


def fold_fixes(claims):
    """His Right / Fix / Wrong from cos, filed as they are: a Fix is his words, pinned, replacing the old fact."""
    folded = json.load(open(f"{R.DATA}/folded.json")) if os.path.exists(f"{R.DATA}/folded.json") else {}
    n = 0
    for f in jsonl(f"{R.DATA}/fixes/{R.PERSON}.jsonl"):
        if f.get("id") in folded or f.get("id") in claims:
            continue
        c = claims.get(f.get("claim", ""))
        at = f.get("at") or ""
        if not c:
            R.FOLDED[f["id"]] = "dropped"
            continue
        if f["op"] == "right":
            c.update(confirmed=at, flag="")
        elif f["op"] == "wrong":
            c.update(flag="wrong", flagged=at, confirmed="")
        elif f["op"] == "fix":
            ref = f"fix:{f['id']}"
            R.SOURCES[ref] = {"type": "fix", "agent": "You, in Knowledge", "machine": "cos", "date": at[:10],
                              "spoke": "his words", "night": NIGHT}
            claims[f["id"]] = {"id": f["id"], "page": c["page"], "section": c["section"], "text": f["text"],
                               "kind": "said", "date": at[:10], "why": "", "rejected": "", "refs": [ref],
                               "status": "current", "replaces": [c["id"]], "person": R.PERSON,
                               "author": f"{R.PERSON}@cos", "pinned": True, "night": NIGHT}
            if c.get("status") == "current":
                c.update(status="superseded", superseded_by=f["id"], superseded_on=NIGHT)
        R.FOLDED[f["id"]] = f["id"] if f["op"] == "fix" else f["claim"]
        n += 1
    return n


def note_candidates(claims, pages):
    """The agents' notes the hub holds and the librarian hasn't filed: candidates for the apply pass, on their page."""
    folded = json.load(open(f"{R.DATA}/folded.json")) if os.path.exists(f"{R.DATA}/folded.json") else {}
    out = []
    for n in jsonl(f"{R.DATA}/notes/{R.PERSON}.jsonl"):
        if n.get("id") in folded or n.get("id") in claims:
            continue
        ref = f"note:{n['id']}"
        R.SOURCES[ref] = {"type": "note", "agent": n.get("agent") or "an agent", "group": n.get("group") or "",
                          "machine": n.get("machine") or "", "chat": n.get("chat") or "", "date": (n.get("at") or "")[:10],
                          "spoke": {"said": "his words", "observed": "agent saw"}.get(n.get("claim_kind") or "") or
                          "relayed" + (f" by {n['via']}" if n.get("via") else ""), "night": NIGHT}
        same = claims.get(n.get("same_as") or "")
        if same:
            same["refs"] = sorted(set(same["refs"] + [ref]))
            R.FOLDED[n["id"]] = same["id"]
            continue
        page = n.get("page") if n.get("page") in pages else f"person/{R.PERSON}"
        out.append({"page": page, "section": n.get("section") or "decisions", "text": n["text"],
                    "kind": n.get("claim_kind") or "said", "ref": ref, "date": (n.get("at") or "")[:10],
                    "why": n.get("why") or "", "rejected": "", "note": n["id"], "replaces": n.get("replaces") or [],
                    "author": f"{n.get('agent') or 'agent'}@{n.get('machine') or '?'}"})
    return out


# ---------------------------------------------------------------- the night's dated export
def export_changes(before, lo, hi):
    """changes/<date>.md in the store: what this run added, replaced and settled, and what was removed since the
    last run, oldest first, in the same words as knowledge_changes. The weekly review reads a week of these."""
    sys.path.insert(0, os.path.expanduser("~/Documents/personal/apps/cos/hub"))
    import knowledge as K
    import knowtools as T
    st = K.Store(R.ROOT, R.PERSON).load()
    lines = []
    for c in sorted((c for cid, c in st.claims.items() if cid not in before), key=lambda c: (c.get("date") or "", c["id"])):
        lines.append(f"+ added {T.fact_line(st.claim_out(c))}")
    for c in sorted((c for cid, c in st.claims.items() if before.get(cid) == "current" and c.get("status") != "current"),
                    key=lambda c: c["id"]):
        lines.append(f"~ {'resolved' if c['status'] == 'resolved' else 'replaced'} {c['id']} ({c['page']}) by "
                     f"{c.get('superseded_by') or '?'}: was \"{c['text']}\"")
    for r in st.removed:
        if lo <= (r.get("at") or "")[:16] < hi:
            lines.append(f"- removed {r['id']} ({r.get('page')}): {r.get('why')}, by {r.get('by') or 'someone'}")
    os.makedirs(f"{R.ROOT}/changes", exist_ok=True)
    path = f"{R.ROOT}/changes/{hi[:10]}.md"
    head = "" if os.path.exists(path) else f"# Knowledge changes · {hi[:10]}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{head}\n## Run {lo} .. {hi} ({len(lines)} changes)\n\n" + ("\n".join(lines) or "Nothing changed.") + "\n")
    return len(lines)


# ---------------------------------------------------------------- run
def main():
    global NIGHT
    args = sys.argv[1:]
    st = load_state()
    hi = args[args.index("--until") + 1] if "--until" in args else datetime.now().strftime("%Y-%m-%dT%H:%M")
    lo = args[args.index("--since") + 1] if "--since" in args else st["last"]
    if not lo:
        raise SystemExit("no last run in data/librarian.json: give --since YYYY-MM-DDTHH:MM")
    NIGHT = f"D{hi[:10]}"
    t0 = time.time()
    log(f"night {NIGHT}: {lo} .. {hi}, store {R.ROOT}")
    counts = collect(st, lo, hi)
    text = "\n\n".join(blocks)
    os.makedirs(f"{R.WORK}/in", exist_ok=True)
    open(f"{R.WORK}/in/{NIGHT}.txt", "w").write(text)
    log(f"collected {len(blocks)} sources (~{len(text) // 4:,} tokens): {counts}; scrub hits {HITS}")
    if "--collect-only" in args:
        return
    pages, claims = R.load()
    before = {cid: c["status"] for cid, c in claims.items()}
    fixes = fold_fixes(claims)
    notes = note_candidates(claims, pages)
    known = set(claims)   # the leak check looks only at facts made tonight
    cands = R.extract(NIGHT, pages, claims, text) if text.strip() else []
    cands = R.route(NIGHT, cands, pages, claims) if cands else []
    cands += notes
    log(f"{len(cands)} candidates ({len(notes)} notes, {fixes} fixes filed) on {len({c['page'] for c in cands})} pages")
    stats = R.apply(NIGHT, cands, pages, claims) if cands else {}
    # last check: nothing that looks like a secret or a scrubbed number goes into the store
    for cid in set(claims) - known:
        bad = leaks(claims[cid]["text"]) or re.search(r"\[(secret[^\]]*|number|ip|email)\]", claims[cid]["text"])
        if bad:
            log(f"dropped {cid}: looks like it holds a secret ({bad})")
            del claims[cid]
    st["last"] = hi
    R.save(pages, claims)
    R.write_atomic(STATE, json.dumps(st, indent=0))
    calls = R.runs
    rec = {"night": NIGHT, "from": lo, "to": hi, "sources": len(blocks), **counts, "notes": len(notes), "fixes": fixes,
           "candidates": len(cands), "ops": stats, "pages": len(pages),
           "current_claims": sum(1 for c in claims.values() if c["status"] == "current"),
           "calls": len(calls), "tokens_in": sum(r["in"] for r in calls), "tokens_out": sum(r["out"] for r in calls),
           "usd_equiv": round(sum(r.get("usd_equiv") or 0 for r in calls), 2), "minutes": round((time.time() - t0) / 60, 1),
           "changes": export_changes(before, lo, hi)}
    nights = json.load(open(f"{R.DATA}/nights.json")) if os.path.exists(f"{R.DATA}/nights.json") else []
    R.write_atomic(f"{R.DATA}/nights.json", json.dumps(nights + [rec], indent=1))
    old = json.load(open(f"{R.DATA}/runs.json")) if os.path.exists(f"{R.DATA}/runs.json") else []
    R.write_atomic(f"{R.DATA}/runs.json", json.dumps(old + [{**r, "night": NIGHT} for r in calls], indent=1))
    log("done", json.dumps(rec))


NIGHT = ""
if __name__ == "__main__":
    main()
