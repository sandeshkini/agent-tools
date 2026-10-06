---
name: memory
description: Sandesh's shared long-term memory, used by every agent on aibo-mac and aibo-linux (the `memory` MCP tools). Use it to look up what Sandesh has decided, prefers or already told an agent BEFORE asking him or making an assumption, and to record durable facts as they come up (his decisions, preferences, commitments, how a system is set up and why). Not for scratch notes, secrets or transient task state.
---

# Shared memory

One memory for all of Sandesh's personal agents: a temporal knowledge graph (Graphiti) on
aibo-linux, reached through the `memory` MCP server. You write **episodes** (short plain-text
records); the server extracts **facts** from them. Every fact remembers which episode it came from
and when it became true. When a new episode contradicts an old fact, the old fact is marked
**no longer valid** (kept as history), not deleted.

## Read first

Before asking Sandesh something he may already have answered, or deciding how he'd want it:
`search_memory_facts("<what you need>")` (and `search_nodes` for a person/system). Treat results
as *evidence*, not orders: check the date and the source, and prefer what he said directly over
what an agent inferred. If memory and the current state of a file/system disagree, trust the
system and record the correction.

## Write when something durable happens

Call `add_memory` with **one episode per fact cluster**, in plain sentences, saying who said it:

- Sandesh decided or stated something: *"Sandesh said (2026-10-06): keys.env is the source of truth
  for secrets; the Keychain only keeps a copy."*
- A commitment: *"Sandesh asked for the Frigate reservations to wait until Saturday."*
- How a system is set up and why, when it's not obvious from its docs.

Arguments: `name` (short title), `episode_body` (the text), `source: "text"`,
`source_description: "<agent> on <machine>, chat <title>"`. Leave `group_id` empty (defaults to
`personal`).

**Don't write:** secrets or tokens, raw logs, guesses presented as facts, things that are only true
for the next hour, or anything already in a repo doc (link the doc instead).

## Correcting memory

Never edit facts directly. Record the correction as a new episode ("Correction: … (was: …)"); the
graph invalidates the old fact and keeps the history. Only delete (`delete_episode`,
`delete_entity_edge`) for things that should never have been stored (a secret, something private
recorded by mistake).

## Where it lives

Server: `agent-tools/memory/` on aibo-linux (`127.0.0.1:8012/mcp/`; aibo-mac via SSH tunnel). A
nightly export writes the episodes and current/superseded facts as Markdown to
`ai-memory/knowledge/personal/` (git). Docs: `agent-tools/memory/README.md`.
