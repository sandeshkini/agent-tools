# cua-driver — computer-use MCP server

[`cua-driver-rs`](https://cua.ai) — a third-party binary (installed via `cua.ai`'s own installer,
not built from this repo, and not something we vendor here). This folder just documents how it's
installed/wired per machine, mirroring how `mcp-tools`/`cptr-watchdog` are documented alongside the
tools they set up, even though the binary itself lives outside `agent-tools`.

Gives every cptr agent (Claude Code, OpenCode) desktop control on the host it runs on: click, type,
screenshot, launch apps, drag, zoom-capture, recording — **56 tools** as of `cua-driver 0.20.0`.
Talks over stdio (`cua-driver mcp`), one instance per machine, no shared state between machines.

## Install (macOS)

```bash
/bin/bash -c "$(curl -fsSL https://cua.ai/driver/install.sh)"
```

Installs `/Applications/CuaDriver.app`, symlinks the binary to `~/.local/bin/cua-driver`. Telemetry
defaults **on** (pseudonymous install ID + bounded content-free usage metadata, no prompts/tool
args/screen contents/paths collected) — disable with `cua-driver telemetry disable` if unwanted.

Version note: aibo-mac's original install (2026-08-14) was `cua-driver-rs 0.19.3`. aibo-dev's
(2026-08-16) pulled `0.20.0` — the installer always fetches latest unless pinned via
`CUA_DRIVER_RS_VERSION`. The two machines are not guaranteed to be on the same version; check with
`cua-driver --version` per machine if that ever matters.

## Permissions (macOS — Accessibility + Screen Recording)

Both must be granted to the **driver's own identity** (`com.trycua.driver`), not your terminal —
checking from a terminal session reports the terminal's grants, which is misleading.

```bash
cua-driver permissions grant     # launches CuaDriver.app itself so every dialog matches its identity
cua-driver permissions status    # read-only check (needs the daemon running to be meaningful)
```

**Gotcha confirmed on aibo-dev (2026-08-16): Screen Recording does not appear in System Settings
at all until the app actually attempts a capture** — requesting Accessibility isn't enough to make
the OS list the entry. If `permissions grant` leaves Screen Recording invisible in System Settings,
force it:

```bash
cua-driver call get_desktop_state   # fails ("could not create image from display"), but the
                                     # attempt itself makes CuaDriver appear in the Screen
                                     # Recording list — then grant it there and re-run this call
```

Also matches the aibo-mac gotcha (documented in `../../aibo-server/Services/cptr/README.md`): a
Screen Recording grant can fail to take effect across several restarts, and `serve`'s own internal
permission-recheck loop (self-restarts ~30s to re-prompt) combined with `launchd` `KeepAlive` can
make one stale, undismissed system dialog look like it's "stuck repeatedly asking" when nothing is
actually running. Fix is the same: dismiss the stale dialog, re-toggle directly in System Settings
(not via the app's own prompt) with no `cua-driver` process alive, start one clean instance, verify
with `permissions status` before restoring the `launchd` service.

## Autostart (macOS — launchd; the binary's own `autostart` subcommand is Windows-only)

```bash
PLIST="$HOME/Library/LaunchAgents/com.trycua.cua-driver.plist"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.trycua.cua-driver</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Applications/CuaDriver.app/Contents/MacOS/cua-driver</string>
    <string>serve</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>StandardOutPath</key><string>/tmp/cua-driver.log</string>
  <key>StandardErrorPath</key><string>/tmp/cua-driver.log</string>
</dict></plist>
PL
launchctl bootout gui/$(id -u)/com.trycua.cua-driver 2>/dev/null
launchctl bootstrap gui/$(id -u) "$PLIST"
```

Verified on aibo-dev (2026-08-16): survives `kill -9` (new PID within ~4s, `KeepAlive`), grants
persist across the relaunch (stable app identity, same as `cptr-input` used to rely on before it
was removed). `RunAtLoad` not separately reboot-tested this session, but same mechanism as
`cptr-watchdog`/`mcp-tools`, both confirmed reboot-safe.

No provenance-xattr concern here (unlike `mcp-tools`'s original bug) — the binary lives under
`/Applications`, not `~/Documents`, so it's outside the xattr's blast radius entirely.

## MCP registration

Uses the plain `cua-driver mcp` stdio convention — **deliberately not** `cua-driver mcp-config
--client claude`'s "computer-use compatibility mode" (that mode swaps the screenshot tool for a
Claude-vision-specific flow; plain `mcp` keeps every tool as-is and is what both CLI agents use
identically). Same convention as aibo-mac.

**Claude Code** — `~/.claude.json` → `mcpServers`:
```json
"cua-driver": {"type": "stdio", "command": "cua-driver", "args": ["mcp"]}
```

**OpenCode** — `~/.config/opencode/opencode.jsonc` (or `.json`) → `mcp`:
```json
"cua-driver": {"type": "local", "command": ["cua-driver", "mcp"], "enabled": true}
```

Verify: `claude mcp list` / `opencode mcp list` → `cua-driver: connected` in both. Confirmed with a
real `get_desktop_state` call returning an actual PNG (not just a "connected" status), same
verification bar as `mcp-tools`.

## Guardrails

No destructive-action guardrail exists for `cua-driver` (unlike Hermes-era `deny-destructive.py`,
long retired). cptr's `toolApprovalMode` (auto/manual) is the only gate on tool calls — consider
`manual` until this is trusted on a given machine, especially since it has real mouse/keyboard/app-
launch control, same reasoning as aibo-mac's README section on it.

## Per-machine status

| Machine | Version | Autostart | Accessibility | Screen Recording | MCP (Claude/OpenCode) |
|---|---|---|---|---|---|
| aibo-mac | 0.19.3 (2026-08-14) | `launchd` KeepAlive | granted | granted | both connected |
| aibo-dev | 0.20.0 (2026-08-16) | `launchd` KeepAlive | granted | granted | both connected |
| aibo-linux | not installed | — | — | — | — |

aibo-linux doesn't need this — it already has `cua-driver` set up differently there (see
`../../aibo-server/Services/cptr/README.md` § MCP integrations; that install predates this repo
folder and isn't duplicated here).
