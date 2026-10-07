# agent-tools

Shared, always-on agent services and tooling for the agent machines (aibo-linux, aibo-mac). Extracted
from `agent-hub` on 2026-08-11 so agent-hub (the retired OWUI stack) could be fully stopped. **cptr**
(aibo-server `infrastructure/agents/cptr/README.md`) is the primary agent UI now and consumes these.
The clone lives at `~/Documents/personal/agent-tools` on every machine. Fleet-wide docs for the agent
tooling are in the (private) aibo-server repo: `infrastructure/agents/README.md` and
`infrastructure/agents/tools.md`.

### Set up a machine: `setup.sh`

One command for every machine, Mac or Linux. It reads a per-machine profile (which tools, cptr's
label/port, where named apps go), reports what's in place and applies the rest using the installers
below. Besides the installers it has two steps of its own: `wiring` (writes `registry.toml` into
every agent program, see below) and `secrets` (renders files that hold secrets, such as a compose
`.env` or `cptr.env`, from `~/Documents/secrets/keys.env` via `bin/fleet-secret`). Details:
[setup/README.md](setup/README.md).

```bash
git clone https://github.com/sandeshkini/agent-tools.git
agent-tools/setup.sh --check      # in place vs the profile; changes nothing
agent-tools/setup.sh              # apply (asks first); --only <component>, --rollback <component>, --list
```

Profiles: `--profile <file>`, `$AGENT_SETUP_PROFILE` or `~/.config/agent-setup/profile.toml`; start
from [setup/profiles/example.toml](setup/profiles/example.toml). Real profiles live in a private
repo, and `--profile-from <repo>` picks the one whose `[match]` fits this host.

### Docker services (`docker compose`)

| Service | Where | URL / port |
|---|---|---|
| `artifacts` | board (publish any content type, versioned) | `artifacts.kingdomofluna.com` (write: `push.artifacts.kingdomofluna.com`) |
| `ntfy` | phone push bus | `ntfy.kingdomofluna.com`, host `:8095` |
| `mcp-tools` | shared MCP: `publish_artifact`/`create_artifact`/`update_artifact`/`list_artifacts`/`publish_files`/`notify` | `127.0.0.1:8009/mcp` (cptr consumes this) |
| `memory` | shared long-term agent memory: Graphiti temporal knowledge graph + FalkorDB, its own MCP (`add_memory`, `search_memory_facts`, …); nightly Markdown export to `ai-memory` | `127.0.0.1:8012/mcp/` on aibo-linux; aibo-mac over SSH. [memory/README.md](memory/README.md) |

### Host tools (`<tool>/install.sh`, not Docker; `setup.sh` runs them per profile)

Needs direct hardware access, so it runs on the host via launchd (macOS) /
systemd (Linux), not in a container.

| Tool | What | Install |
|---|---|---|
| `cptr-watchdog` | periodic self-heal for cptr's own service — re-registers it if the launchd job/systemd unit was deregistered entirely (not just crashed; `KeepAlive`/`Restart=always` don't cover that), restarts it if registered but unhealthy | `cptr-watchdog/install.sh` |
| `computer-use` | **Macs only:** cua-driver + its daemon (desktop apps) and agent-browser (web pages), plus the optional Agent Chrome (CDP :9333). Installs the tools only; their MCP entries and skills come from `registry.toml` via `setup.sh --only wiring`. Linux (aibo-linux) uses computer-use-linux + agent-browser instead, also wired from the registry; cua-driver was removed there 2026-09-25 | `computer-use/install.sh [--agent-chrome]`, then `cua-driver permissions grant` |
| `stuck-watch` | notices agent commands silently stuck on a macOS permission prompt / unanswered dialog (idle agent commands, unanswered TCC prompts, password/permission windows) and sends one ntfy push per issue; read-only, never kills or clicks. Any Mac | `stuck-watch/install.sh [--check\|--uninstall]` |
| `mac-apps` | runs cptr, the cptr watchdog and Stuck Watch (optionally session sync and mcp-tools) as named apps (`cptr.app`, `cptr Watchdog.app`, `Stuck Watch.app`) so privacy lists show their names instead of python3.x/bash and cptr's Full Disk Access belongs to cptr alone; grants FDA first, switches detached, health-gated with rollback. Also **the one named-app builder** (launcher, signing, probe) that Personal Agent's `pa-app` uses. Any Mac | `mac-apps/install.sh --check`, then `--all` (at the Mac), or through `setup.sh` |
| `mcp-tools` (host mode) | same `publish_artifact`/`notify` MCP as the Docker service above, for a machine with no Docker (thin client hitting aibo's shared board/bus over the public URLs, not the compose network). On aibo-mac it runs as the named app `com.sandesh.pa.agent-tools-mcp` | `mcp-tools/install.sh` |
| `sync-ai-sessions` | exports Claude Code + OpenCode transcripts and memory into the `ai-memory` repo (`~/Documents/personal/ai-memory`). One script for both machines. aibo-mac: named app `com.sandesh.pa.ai-session-sync`; aibo-linux: systemd `--user` timer | `sync-ai-sessions/install.sh` (Linux and macOS), see its README |
| `fleet-tunnel` | the aibo-mac → aibo-linux SSH tunnels (shared memory :8012, aibo-linux's cos node :8791) as their own job with a tunnel-only key, replacing cos-node's `--tunnel` flags. **Not switched on on aibo-mac yet**: cos-node still holds the tunnels there | `fleet-tunnel/install.sh [--check\|--uninstall]`; key trusted on aibo-linux with `fleet-tunnel/authorize.sh`. Docs: aibo-server `infrastructure/agents/tools.md` § tunnels |

See `cptr-watchdog/README.md`/`mcp-tools/README.md`. Both carry
per-machine service state (paths/ports/versions differ per host), so installers generate
the launchd/systemd unit rather than shipping one checked in.

### Tool registry: `registry.toml` + `setup.sh --only wiring`

`registry.toml` is the one list of MCP servers, skills, Claude Code plugins and helper commands the
agents on each machine get, with per-machine/OS/program filters. `setup.sh`'s `wiring` step writes it into Claude Code
(`claude mcp`, user scope), OpenCode (`opencode.json[c]`), Antigravity (`agy mcp`) and the skill
folders (`~/.claude/skills`, `~/.config/opencode/skills`, `~/.agents/skills` for cptr and Codex,
`~/.gemini/config/skills` for agy), installs the listed Claude Code plugins, and links `bin/` commands
into `~/.local/bin`. `setup.sh --check`
reports drift. Add tools to the registry, not by hand with `claude mcp add` or by editing
`opencode.json`. Docs: aibo-server
`infrastructure/agents/tools.md`.

### Commands: `bin/`

| Command | What |
|---|---|
| `fleet-secret` (alias `pa-secret`) | the secrets command on every machine. `~/Documents/secrets/keys.env` is the store; on macOS every write is mirrored to the login Keychain. `get`/`set`/`run NAME -- cmd`/`render TEMPLATE OUT`/`sync`/`backup`. Docs: aibo-server `infrastructure/secrets.md` |
| `agy-ask` | ask Antigravity (agy) one question from another agent, safely: runs it in a scratch folder holding only copies of the files passed. Used by the `agy` skill. Was `pa-agy` in personal-agent |
| `ui-shot` | screenshot a web page at several sizes and themes through agent-browser, so agents look at UI they changed. Used by the `ui-check` skill. Was `pa-shot` in personal-agent |

### Skills: `skills/`

The one folder for every skill we write, shared or machine-specific (`linux-operator` is aibo-linux
only, `mac-operator` aibo-mac only; the registry decides who gets what). The `memory` skill is in
`memory/skill/`. Full list, and how to add one: [skills/README.md](skills/README.md).

> `loopback-shim`, a host-level fix for an IPv4/IPv6 quirk in **cptr's own**
> built-in chrome-mode viewer, lives with the rest of the cptr hub docs at aibo-server
> `infrastructure/agents/cptr/host-fixes/loopback-shim/` instead of here, since it fixes cptr itself rather than
> adding a shared tool.

> **`cptr-input`** (desktop streaming + input-injection daemon) was removed
> 2026-08-16. We decided to stick with RustDesk for all computer-input/remote
> desktop needs instead. Source deleted from this repo, and confirmed removed on both aibo-dev and
> aibo-mac (2026-08-16). If you find a stray reference elsewhere, it's stale;
> RustDesk is the one true answer now (see `infrastructure/rustdesk.md` in
> `aibo-server`).

## Run
```bash
docker compose up -d --build     # docker services: start / rebuild
docker compose ps                # status
docker compose down              # stop
```
Secrets in `.env` (gitignored): `PUBLISH_TOKEN`, `NTFY_URL/TOPIC/TOKEN`, `OPENROUTER_API_KEY` (memory).
Where the machine's profile has a `[secrets.render]` entry for it, `.env` is generated by
`setup.sh --only secrets` from keys.env, so change the value with `fleet-secret set` and re-render
rather than editing `.env`. Data (gitignored):
`artifacts/content/`, `ntfy/data/` (incl. the ntfy auth `user.db` — preserve across moves).
`apps` = the external Traefik network. Host-tool build artifacts (`**/.venv/`) are gitignored —
regenerated by the installers.
**Expand here** as we add shared tools.
