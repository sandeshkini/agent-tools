# memory — shared long-term memory for every agent

One memory for all of Sandesh's personal agents (Claude Code and OpenCode, on aibo-mac and
aibo-linux, including the Chief of Staff), built on **[Graphiti](https://github.com/getzep/graphiti)**,
an off-the-shelf temporal knowledge graph, using its official MCP server. Chosen 2026-10-06 because
it's built for *memory that stays correct over time*:

- **Provenance.** Agents write **episodes** (short plain-text records: who said what, when). Every
  extracted **fact** links back to the episode it came from.
- **Time.** Every fact has *valid from / valid until* and *when we learned it*. A newer episode that
  contradicts a fact marks the old one invalid. It's **kept as history, not deleted**.
- **A readable long-term record.** A nightly export writes all episodes plus current/superseded
  facts as Markdown into the `ai-memory` git repo. Episodes are the source of truth; the graph is
  derived and can be rebuilt from them (e.g. with a better model later).

(mem0 was the alternative; it resolves conflicts by silently deleting, with no history. Basic
Memory is plain files with no correctness checks.)

## Pieces

| Piece | Where |
|---|---|
| `memory` container: Graphiti MCP server + FalkorDB, image `zepai/knowledge-graph-mcp:1.1.0-graphiti-0.30.1` | aibo-linux, agent-tools `docker-compose.yml`. MCP `127.0.0.1:8012/mcp/`, graph browser `127.0.0.1:3012`. Data `memory/data/` (gitignored) |
| `config.yaml` | Provider + entity types (Preference, Decision, Commitment, System, Person, …), mounted into the container |
| Models | OpenRouter (`OPENROUTER_API_KEY` in agent-tools `.env`; source `secrets/keys.env` on aibo-mac). Extraction `google/gemini-3.8-flash` (`MEMORY_MODEL` to change; needs structured outputs), embeddings `openai/text-embedding-3-small` |
| Memory space | group `personal`. The future work Chief of Staff gets its own group (and server) |
| `export.py` + `install.sh` | Nightly 01:30 (`memory-export.timer`, systemd --user) → `~/Documents/personal/ai-memory/knowledge/personal/{episodes/YYYY-MM.md, facts.md, superseded.md}`, committed; aibo-linux's 02:30 `backup.sh` pushes it |
| `standing-rules.md` | Sandesh's short, checked list of standing rules. Loaded into every agent's instructions (`[instructions.standing-rules]` in `registry.toml` → a managed `@import` in `~/.claude/CLAUDE.md` and OpenCode's `instructions`). Memory holds the details; this list is what must never be missed by a search. Changed only when Sandesh says so |
| `skill/SKILL.md` | The `memory` skill: when to read, what to write, how to correct. Listed as `[skills.memory]` in `registry.toml`; `setup.sh --only wiring` links it into `~/.claude/skills`, `~/.config/opencode/skills`, `~/.agents/skills` and agy's skills folder on both machines |
| `selftest.py` | Writes two contradicting episodes into a scratch group and checks the old fact is superseded, then clears it |

## Access (no auth of its own, so never on the network)

- The MCP server listens on aibo-linux's **127.0.0.1:8012 only**.
- Registered as `memory` → `http://127.0.0.1:8012/mcp/` by `[mcp.memory]` in `registry.toml`, which
  `setup.sh --only wiring` writes into Claude Code (`~/.claude.json`), OpenCode
  (`~/.config/opencode/opencode.json[c]`) and agy on aibo-linux and aibo-mac.
- aibo-mac agents: the same URL on aibo-mac, carried by an **SSH tunnel** to aibo-linux. Today
  aibo-mac's cos-node keeps it up (`apps/cos/node/install.sh --name aibo-mac --tunnel 8791=… --tunnel
  8012=beastblaster@192.168.0.146:8012`). **Moving (pending):** the tunnel is to become its own job,
  [`fleet-tunnel/`](../fleet-tunnel/install.sh), with a tunnel-only key, replacing cos-node's
  `--tunnel` flags. It isn't switched on on aibo-mac yet.
- Nothing connects *into* aibo-mac.

## Tools agents get

`add_memory` (record an episode; processed in the background, ~1 min), `search_memory_facts`,
`search_nodes`, `get_episodes`, `get_entity_edge`, `get_episode_entities`, `delete_episode`,
`delete_entity_edge`, `summarize_saga`, `build_communities`, `add_triplet`, `get_status`,
`clear_graph` (scratch groups only!).

## Run

```bash
# aibo-linux, in ~/Documents/personal/agent-tools
docker compose up -d memory                 # start / update (pinned image)
docker logs -f memory
memory/install.sh                           # (re)install the nightly export timer
memory/install.sh --check                   # health + timer + last export
uv run --with "mcp>=1.12,<2" python memory/selftest.py   # ~2 min, a few cents
ssh -L 3012:127.0.0.1:3012 beastblaster@192.168.0.146    # then open http://localhost:3012 for the graph browser
```

## Keeping it sound (memory review, 2026-10-10)

- **Store less.** Only Sandesh's rules and preferences, his decisions (with the reason), commitments,
  and non-obvious setup. No progress reports, no "pending / waits for his OK" (they never get closed
  and show up later as stale facts), nothing already in a doc.
- **One writer.** The agent that heard it from Sandesh records it; agents that get a decision passed
  on (from the Chief or via crew) don't record it again. Everyone searches before writing. A relayed
  message says "(saved to memory)" once it's recorded.
- **Short episodes.** One topic, 1–4 facts, ≤ ~600 characters: above ~1,000 characters extraction
  yields about half the facts per character.
- **Closing things.** `Open: X` … later `Done: X (date). Was: open since …`, same words, so Graphiti
  links them and invalidates the old fact. A "done" note that doesn't name the open item leaves the
  old fact valid (seen with the Pangolin restart and the keys.env escrow).
- Corrections are new episodes, never edits. Deletes and merges only with Sandesh's OK.
- `get_episodes` returns episodes in uuid order, not newest first; use the nightly export for a dated list.

Audit at the time (2026-10-10): 111 episodes, 645 facts, 311 entities in 5 days; ~15 decisions
recorded twice (Chief + the agent doing the work); 6 stale "pending" facts; extraction with
`gemini-3.8-flash` at ≈ $0.07 per episode (≈10 LLM calls each), $13.66 in October so far.
