# agent-tools

Shared, always-on agent services on aibo. Extracted from `agent-hub` on 2026-08-11 so agent-hub
(the retired OWUI stack) could be fully stopped. **cptr** (`~/Documents/aibo-server/Services/cptr/README.md`)
is the primary agent UI now and consumes these.

### Set up a machine: `setup.sh`

One command for every machine, Mac or Linux. It reads a per-machine profile (which tools, cptr's
label/port, where named apps go), reports what's in place and applies the rest using the installers
below. Details: [setup/README.md](setup/README.md).

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

### Host tools (`<tool>/install.sh`, not Docker; `setup.sh` runs them per profile)

Needs direct hardware access, so it runs on the host via launchd (macOS) /
systemd (Linux), not in a container.

| Tool | What | Install |
|---|---|---|
| `cptr-watchdog` | periodic self-heal for cptr's own service — re-registers it if the launchd job/systemd unit was deregistered entirely (not just crashed; `KeepAlive`/`Restart=always` don't cover that), restarts it if registered but unhealthy | `cptr-watchdog/install.sh` |
| `computer-use` | cua-driver (desktop apps) + agent-browser (web pages) + the optional Agent Chrome (CDP :9333), skills and MCP wiring, for any Mac. What aibo-mac runs | `computer-use/install.sh [--agent-chrome]`, then `cua-driver permissions grant` |
| `stuck-watch` | notices agent commands silently stuck on a macOS permission prompt / unanswered dialog (idle agent commands, unanswered TCC prompts, password/permission windows) and sends one ntfy push per issue; read-only, never kills or clicks. Any Mac | `stuck-watch/install.sh [--check\|--uninstall]` |
| `mac-apps` | runs cptr, the cptr watchdog and Stuck Watch (optionally session sync and mcp-tools) as named apps (`cptr.app`, `cptr Watchdog.app`, `Stuck Watch.app`) so privacy lists show their names instead of python3.x/bash and cptr's Full Disk Access belongs to cptr alone; grants FDA first, switches detached, health-gated with rollback. Also **the one named-app builder** (launcher, signing, probe) that Personal Agent's `pa-app` uses. Any Mac | `mac-apps/install.sh --check`, then `--all` (at the Mac), or through `setup.sh` |
| `mcp-tools` (host mode) | same `publish_artifact`/`notify` MCP as the Docker service above, for a machine with no Docker (thin client hitting aibo's shared board/bus over the public URLs, not the compose network) | `mcp-tools/install.sh` |

See `cptr-watchdog/README.md`/`mcp-tools/README.md` — both carry
per-machine service state (paths/ports/versions differ per host), so installers generate
the launchd/systemd unit rather than shipping one checked in.

> `loopback-shim`, a host-level fix for an IPv4/IPv6 quirk in **cptr's own**
> built-in chrome-mode viewer, lives with the rest of the cptr hub docs at
> `~/Documents/aibo-server/Services/cptr/host-fixes/loopback-shim/` instead of
> here, since it fixes cptr itself rather than adding a shared tool.

> **`cptr-input`** (desktop streaming + input-injection daemon) was removed
> 2026-08-16 — decided to stick with RustDesk for all computer-input/remote
> desktop needs instead. Source deleted from this repo. **aibo-dev: confirmed
> removed** (2026-08-16 — `com.sandesh.cptr-input`/`com.sandesh.agent-desktop`
> launchd jobs booted out, plists deleted, no process/socket left). **aibo-mac:
> still needs manual removal** — no remote access to that machine from here to
> confirm or do it. If you find a stray reference elsewhere, it's stale;
> RustDesk is the one true answer now (see `Infrastructure/rustdesk.md` in
> `aibo-server`).

## Run
```bash
docker compose up -d --build     # docker services: start / rebuild
docker compose ps                # status
docker compose down              # stop
```
Secrets in `.env` (gitignored): `PUBLISH_TOKEN`, `NTFY_URL/TOPIC/TOKEN`. Data (gitignored):
`artifacts/content/`, `ntfy/data/` (incl. the ntfy auth `user.db` — preserve across moves).
`apps` = the external Traefik network. Host-tool build artifacts (`**/.venv/`) are gitignored —
regenerated by the installers.
**Expand here** as we add shared tools.
