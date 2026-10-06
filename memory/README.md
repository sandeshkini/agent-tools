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
| `export.py` + `install.sh` | Nightly 01:30 (`memory-export.timer`, systemd --user) → `~/Documents/ai-memory/knowledge/personal/{episodes/YYYY-MM.md, facts.md, superseded.md}`, committed; aibo-linux's 02:30 `backup.sh` pushes it |
| `skill/SKILL.md` | The `memory` skill: when to read, what to write, how to correct. Linked into `~/.claude/skills`, `~/.config/opencode/skills`, `~/.agents/skills` on both machines (aibo-mac via `personal-agent/skills/memory` → `pa-skills-link`) |
| `selftest.py` | Writes two contradicting episodes into a scratch group and checks the old fact is superseded, then clears it |

## Access (no auth of its own, so never on the network)

- The MCP server listens on aibo-linux's **127.0.0.1:8012 only**.
- aibo-linux agents: registered as `memory` → `http://127.0.0.1:8012/mcp/` in `~/.claude.json`
  (`claude mcp add --scope user --transport http memory …`) and `~/.config/opencode/opencode.json`.
- aibo-mac agents: the same URL on aibo-mac, carried by an **SSH tunnel** that aibo-mac's cos-node
  keeps up (`apps/cos/node/install.sh --name aibo-mac --tunnel 8791=… --tunnel
  8012=beastblaster@192.168.0.146:8012`). Registered in `~/.claude.json` and
  `~/.config/opencode/opencode.jsonc`.
- Nothing connects *into* aibo-mac.

## Tools agents get

`add_memory` (record an episode; processed in the background, ~1 min), `search_memory_facts`,
`search_nodes`, `get_episodes`, `get_entity_edge`, `get_episode_entities`, `delete_episode`,
`delete_entity_edge`, `summarize_saga`, `build_communities`, `add_triplet`, `get_status`,
`clear_graph` (scratch groups only!).

## Run

```bash
# aibo-linux, in ~/Documents/agent-tools
docker compose up -d memory                 # start / update (pinned image)
docker logs -f memory
memory/install.sh                           # (re)install the nightly export timer
memory/install.sh --check                   # health + timer + last export
uv run --with "mcp>=1.12,<2" python memory/selftest.py   # ~2 min, a few cents
ssh -L 3012:127.0.0.1:3012 beastblaster@192.168.0.146    # then open http://localhost:3012 for the graph browser
```

Keeping it sound: the Chief of Staff records Sandesh's decisions as they happen and runs a weekly
review ("added / superseded this week, confirm?"). Corrections are recorded as new episodes, never
by editing facts. Deletion only for things that must not be stored.
