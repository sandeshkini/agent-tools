# mcp-tools — shared agent tools (MCP server)

One shared MCP server for every agent that reaches this machine (cptr's Claude Code + OpenCode;
formerly Hermes too, retired 2026-08-11 — see `~/Documents/aibo-server/_retired/agent-hub/`).
Define a tool once here → every agent gets it, no per-adapter work.

- `server.py` — FastMCP (`mcp[cli]`), Streamable HTTP at `:8000/mcp`. Tools:
  - `publish_artifact(title, content_markdown, artifact_type="markdown", language="")` — one-shot
    publish (dated file, no id) to the artifacts board, any content type, auto-fires an ntfy push.
  - `create_artifact(title, content, artifact_type="markdown", language="")` — same, but with a
    stable id for content you'll revise later.
  - `update_artifact(artifact_id, content, language="")` — pushes a new version to an existing id's
    same URL.
  - `list_artifacts()` — newest-first text listing, to find an id before calling `update_artifact`.
  - `notify(title, message, priority)` — bare ntfy phone push, no page.
- Runs as the `mcp-tools` compose service (networks: `default` + `apps`; host port `127.0.0.1:8009`).
- Creds/config via env (compose `.env`): `PUBLISH_TOKEN`, `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN`,
  plus `SOURCE_LABEL`/`COMPUTER_LABEL` — composed into a single `source` field (e.g.
  `cptr@aibo-linux`) auto-stamped on everything this instance publishes, so the board can show/
  filter "who sent this" without the calling agent ever declaring it.

## Add a tool
Add an `@mcp.tool()` function → `docker compose up -d --build mcp-tools`. Every agent gets it on
its next fresh MCP connection (a new session) — no cptr restart needed, cptr doesn't mediate this.

## Wiring (how each agent reaches it)
Each CLI agent gets this server from its **own native MCP config** — not from cptr, which does
not forward tool-server config to the CLIs it shells out to (`~/.cptr/app.db`'s `tool_servers` key
is a documented no-op, tried once and removed):

| CLI agent | Config file | Key |
|---|---|---|
| Claude Code | `~/.claude.json` | `mcpServers.agent-tools` → `{"type":"http","url":"http://127.0.0.1:8009/mcp"}` |
| OpenCode | `~/.config/opencode/opencode.json` | `mcp.agent-tools` → `{"type":"remote","url":"http://127.0.0.1:8009/mcp","enabled":true}` |

Full detail: `~/Documents/aibo-server/Services/cptr/README.md` § MCP integrations.

A second machine without Docker (e.g. a Mac not running the compose stack) runs this via
`install.sh` instead — see that script; same config-file wiring, pointed at its own local
`127.0.0.1:8009/mcp`.
