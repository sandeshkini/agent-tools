# librarian: keeps cos Knowledge, the owner's wiki

cos Knowledge is the one knowledge store for Sandesh's agents. The Graphiti `memory` MCP is retired in its favour.
The librarian builds the wiki from what his agents did. The cos hub reads it, and agents use it through crew/fleet:
`knowledge_search`, `knowledge_read` and `knowledge_note` (cos `docs/knowledge.md`).

| | |
|---|---|
| **Store** | `~/Documents/personal/ai-memory/knowledge/personal/wiki/` (`data/pages.json`, `data/claims/sandesh.jsonl`, `sources.json`, `data/wiki/*.md`, `data/tree.json`, `data/links.json`). ai-memory is a data repo, so `sync-repos` commits and pushes it every 10 minutes. The cos hub mounts it at `/knowledge` (aibo-mac's compose override) |
| **The hub writes** | only `data/fixes/sandesh.jsonl` (his Right / Fix / Wrong) and `data/notes/sandesh.jsonl` (agents' `knowledge_note`), append-only |
| **The librarian writes** | everything else, atomically (the hub reads while it writes), plus `data/folded.json` (the fixes and notes it has filed) and `data/librarian.json` (what it has read) |
| **Nightly** | pa-app **Librarian Nightly** (`com.sandesh.pa.librarian-nightly`), 03:00, `nightly.py`, log `/tmp/pa.librarian-nightly.log` |
| **Weekly** | pa-app **Librarian Weekly** (`com.sandesh.pa.librarian-weekly`), Sun 04:00, `weekly.py`, log `/tmp/pa.librarian-weekly.log` |
| **Model** | Sonnet 5.5 at medium on his plan (`claude-sandesh`), no tools, no MCP servers. Rules: `LIBRARIAN.md` |
| **Cost** | in `data/nights.json`, per run: calls, tokens, and `usd_equiv` (what the CLI says it would cost at API rates; billed to the plan) |

## Nightly (`nightly.py`)

1. **Collect** (no model) everything new since the last run:
   - chat turns from `ai-memory/sessions` (both machines), as the conversation in order: every line of his own
     (up to 2,500 chars, keeping the start and the last quarter), each preceded by the agent reply he was answering
     (up to 900 chars) and that run's keyword lines, plus the final reply (1,500). The work Mac's chats stay out.
     A chat that runs for days is read a turn at a time; `librarian.json` counts the turns already read;
   - crew messages and the hub's agent names, from aibo-linux over SSH (`remote_collect.py`, scrubbed there with
     its own keys.env);
   - the cards he answered (`cos.db`, read-only through `docker exec`);
   - new group ledger lines;
   - any memory episodes from Graphiti's export or `imports/`.

   Lines in his turn that a skill, the harness or the hub wrote (skill bodies, session summaries, routine prompts,
   hub notices) are left out. Everything is scrubbed with this Mac's keys.env (`scrub.py`) and becomes source blocks with refs in `sources.json`.
2. **File his fixes** (no model). A Fix becomes his words, pinned (`author sandesh@cos`), and replaces the old fact.
   Right and Wrong set `confirmed` and `flag`.
3. **Extract, route, apply** (`core.py`). Extract runs in chunks of 30k chars (up to 4 at once; a longer chat is cut
   at his lines into parts) and also keeps his preferences, opinions and passing reasons, replies he accepted, and
   facts about him said in passing (business, family, home, money), not only formal decisions. A candidate that
   repeats a fact but adds a detail is an `enrich`: the fact is rewritten to hold both and keeps every source. Every
   candidate that doesn't make it in (dropped, no page, no op) goes to `data/dropped.jsonl` with the reason. Agents' notes join the apply pass as candidates on their page and keep
   their id when added. Tidy and split never merge or replace a pinned fact.
4. **Check and save.** A leak check runs on every new fact, then the store is saved and the run logged in `nights.json`.

## Weekly (`weekly.py`)

1. **Tidy:** consolidate pages with more than 32 facts.
2. **Split:** give pages still over 30 facts sub-pages (`restructure.py`).
3. **Aliases:** refresh page aliases (`link.py`).
4. **Rewrite:** write fresh prose for every page whose facts changed since it was last written (`write_wiki.py`; `--all`
   rewrites every page). Each page is an article: a `:::box` fact box, a lead with no heading, then two to five
   sections chosen for the subject. Decisions, lessons, open items and history aren't written as prose; cos draws
   them under the article as "the record", straight from the facts. His fixes are pinned, so the prose keeps their
   wording.

## By hand

```sh
cd ~/Documents/personal/agent-tools/librarian
python3 -I nightly.py --collect-only        # see tonight's input in /tmp/librarian/in, change nothing
python3 -I nightly.py                       # a run now, since the last one
python3 -I write_wiki.py system/memory      # rewrite one page
launchctl kickstart gui/$(id -u)/com.sandesh.pa.librarian-nightly
```

**Under launchd:**
- the job uses its own SSH key with no agent;
- its app has Local Network access (System Settings, Privacy & Security, Local Network: *Librarian Nightly*);
  without it, SSH to aibo-linux fails with "No route to host".

The research behind this, with the first full run and its design calls, is in aibo-server
`research/cos-next/knowledge/`.
