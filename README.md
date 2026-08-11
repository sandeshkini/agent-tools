# agent-tools

Shared, always-on agent services on aibo. Extracted from `agent-hub` on 2026-08-11 so agent-hub
(the retired OWUI stack) could be fully stopped. **cptr** (`~/Documents/aibo-server/Services/cptr.md`)
is the primary agent UI now and consumes these.

| Service | Where | URL / port |
|---|---|---|
| `artifacts` | board (publish `.md`/`.html`) | `apps.kingdomofluna.com/artifacts` |
| `ntfy` | phone push bus | `ntfy.kingdomofluna.com`, host `:8095` |
| `mcp-tools` | shared MCP: `publish_artifact` + `notify` | `127.0.0.1:8009/mcp` (cptr consumes this) |

## Run
```bash
docker compose up -d --build     # start / rebuild
docker compose ps                # status
docker compose down              # stop
```
Secrets in `.env` (gitignored): `PUBLISH_TOKEN`, `NTFY_URL/TOPIC/TOKEN`. Data (gitignored):
`artifacts/content/`, `ntfy/data/` (incl. the ntfy auth `user.db` — preserve across moves).
`apps` = the external Traefik network. **Expand here** as we add shared tools (e.g. move multi-machine
node registry off agent-hub later).
