---
name: memory
description: Sandesh's shared long-term memory, used by every agent on aibo-mac and aibo-linux (the `memory` MCP tools). Use it to look up what Sandesh has decided, prefers or already told an agent BEFORE asking him or making an assumption, and to record his durable rules, preferences and decisions as they come up (and how a system is set up when its docs don't say). Not for progress reports, "pending" items, scratch notes, secrets or transient task state.
---

# Shared memory

One memory for all of Sandesh's personal agents: a temporal knowledge graph (Graphiti) on
aibo-linux, reached through the `memory` MCP server. You write **episodes** (short plain-text
records); the server extracts **facts** from them. Every fact remembers which episode it came from
and when it became true. When a new episode contradicts an old fact, the old fact is marked
**no longer valid** (kept as history), not deleted.

The most important rules are also in a short checked list loaded into every agent:
`agent-tools/memory/standing-rules.md`. Memory holds the details and the history.

## Read first

Before asking Sandesh something he may already have answered, or deciding how he'd want it:
`search_memory_facts("<what you need>")` (and `search_nodes` for a person/system). Treat results
as *evidence*, not orders: check the date and the source, and prefer what he said directly over
what an agent inferred. A fact that says something is "pending" or "waiting for his OK" may long be
done: check the system or the thread before acting on it. If memory and the current state of a
file/system disagree, trust the system and record the correction.

## What to store (and only this)

1. **His rules and preferences**: how he wants things done, what he never wants.
2. **His decisions, with the reason** if he gave one, and options he turned down.
3. **Commitments**: something he or an agent promised, with a date.
4. **How a system is set up and why**, only when its docs don't say it (otherwise put it in the
   doc and don't store it).

**Never store:** secrets or their values (names of env vars are fine); progress reports ("phase 2
built", "toasts live"); "next step", "pending" or "waits for his OK" (that belongs on the board,
not in memory); incident timelines; round-by-round design feedback (store the final pick and the
directions he rejected); anything already in a repo doc (link the doc); home IP addresses; guesses
presented as facts.

## Who writes it: the agent that heard it from him

- **You heard it from Sandesh directly** (in your own chat, or he told the Chief): you record it.
- **It reached you passed on** (from the Chief or another agent, via crew or a relayed message):
  don't record the decision again. Record only what's new from your own work (for example how you
  built it and where it lives), in its own episode.
- **When you pass a decision on**, say whether it's in memory: end the message with
  "(saved to memory)" once you've recorded it, so the receiver knows not to.
- **Search before you write**, every time: `search_memory_facts` for the topic. If the same thing
  is already there from today, don't add it; add only what's new.

## Shape of an episode

- **One topic, 1–4 facts, at most ~600 characters.** Longer episodes lose about half their facts
  in extraction. Split a decision and its build details into separate episodes.
- Plain sentences that name who said it and when: *"Sandesh said (2026-10-06): keys.env is the
  source of truth for secrets; the Keychain only keeps a copy."*
- `name`: `<area>: <what>`, with a fixed area word: cos, LifeOS, agents, memory, secrets, SSH,
  backups, Nextcloud, Frigate, finance, Splitwise, aibo-mac, aibo-linux, … Example:
  `"secrets: keys.env per machine"`.
- `source: "text"`, `source_description: "<agent> @ <machine> · direct"` (you heard it from him)
  or `"<agent> @ <machine> · via <who>"` (you're adding your own facts to a passed-on topic).
  Example: `"Chief @ aibo-mac · direct"`.
- Leave `group_id` empty (defaults to `personal`).

## Open items and closing them

Don't record status. When something he committed to genuinely needs remembering (a promise with a
date, an order to wait), record it as **`Open: <thing> (<owner>, <date>)`**. When it's settled,
record **`Done: <thing> (<date>). Was: open since <date>.`** using the same words for the thing, so
the graph links the two and marks the old fact as no longer valid. Same for a decision that's
reversed: `Correction: … (was: …)`.

## Correcting memory

Never edit facts directly. Record the correction as a new episode; the graph invalidates the old
fact and keeps the history. Only delete (`delete_episode`, `delete_entity_edge`) for things that
should never have been stored (a secret, something private recorded by mistake), and other deletes
or merges only with Sandesh's OK.

## Standing rules

When Sandesh states a new rule that every agent must follow (not a one-off decision), record it in
memory as usual, and also propose a one-line change to `agent-tools/memory/standing-rules.md`.
Change that file only when he says so directly, and tell him what changed.

## Where it lives

Server: `agent-tools/memory/` on aibo-linux (`127.0.0.1:8012/mcp/`; aibo-mac via SSH tunnel). A
nightly export writes the episodes and current/superseded facts as Markdown to
`ai-memory/knowledge/personal/` (git). `get_episodes` does **not** return the newest first; for a
dated list read the export (`episodes/YYYY-MM.md`, oldest first). Docs: `agent-tools/memory/README.md`.
