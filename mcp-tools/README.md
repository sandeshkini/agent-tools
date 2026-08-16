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
  - `publish_files(title, files)` — publish one or more local files as a single artifact: a whole
    webpage export (HTML+CSS+JS+images), an app bundle, or just one arbitrary file (PDF, image,
    zip, anything). Each file is read off local disk by *this server*, base64-encoded here, and
    POSTed to `/api/publish-files`, which decodes and writes it byte-exact — content never passes
    through an agent's own text generation, so it can't get corrupted the way a hand-typed base64
    blob can (see `Services/artifacts.md` for the incident that motivated this). `local_path` must
    resolve on *this* deployment: the Docker instance mounts `/tmp:/tmp:ro` only (stage files
    there first), a host install (`install.sh`) has full local filesystem access already. `files`
    is a real typed schema (`FileSpec[]` — `local_path` required, `dest_path` optional), not a
    bare untyped list — see "Schema quality" below for why that distinction was worth fixing.
  - `notify(title, message, priority)` — bare ntfy phone push, no page.
- Runs as the `mcp-tools` compose service (networks: `default` + `apps`; host port `127.0.0.1:8009`).
- Creds/config via env (compose `.env`): `PUBLISH_TOKEN`, `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN`,
  plus `SOURCE_LABEL`/`COMPUTER_LABEL` — composed into a single `source` field (e.g.
  `cptr@aibo-linux`) auto-stamped on everything this instance publishes, so the board can show/
  filter "who sent this" without the calling agent ever declaring it.

## Add a tool
Add an `@mcp.tool()` function → `docker compose up -d --build mcp-tools`. Every agent gets it on
its next fresh MCP connection (a new session) — no cptr restart needed, cptr doesn't mediate this.

## Schema quality — use `Annotated[T, Field(description=...)]`, not `:param` docstring lines

Verified live (2026-08-16) with a raw MCP `tools/list` call against this server: `:param` lines
in a tool's docstring do **not** reach the wire schema at all — only the whole-function docstring
shows up as the tool-level `description` string. Every parameter's `inputSchema` property came
back as a bare `{title, type}` with no `description`, and an untyped `files: list` parameter came
back as `{"items": {}}` — no shape whatsoever for a calling model to go on beyond prose it may or
may not read closely.

The fix, applied to every tool here: annotate each parameter with
`Annotated[T, Field(description="...")]`, use `Literal[...]` for closed value sets
(`artifact_type`, `priority` — these now show up as real JSON Schema `enum` arrays), and a
`pydantic.BaseModel` for structured parameters (`publish_files`' `files: List[FileSpec]` — each
field gets its own description, required/optional is explicit, and it resolves as a proper
`$ref`'d object schema instead of an opaque array). Confirmed via the same raw `tools/list` probe
after the change, plus a live `tools/call`. Keep using this pattern for anything added here —
`:param` docstring prose is still worth keeping for the humans/models reading the tool
description as a whole, but it is not a substitute for the schema itself.

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
