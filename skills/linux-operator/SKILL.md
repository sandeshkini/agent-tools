---
name: linux-operator
description: Operate aibo-linux (Sandesh's always-on Ubuntu home server, hostname beastblaster-M9S) — its real GNOME desktop, the agent's own Chrome, Docker stacks, systemd services, secrets and cptr itself. Use for any desktop, browser, service or machine task on aibo-linux, before restarting anything, and before touching accessibility or screen-reader settings. Desktop goes through the computer-use-linux MCP, web pages through agent-browser; this skill adds the machine-specific facts and hard-won rules on top of those.
---

# Operating aibo-linux

This skill lives in agent-tools (`skills/linux-operator`, aibo-linux only in `registry.toml`). The
how-and-why of the machine is in `~/Documents/personal/aibo-server/machines/personal/aibo-linux/`
(`README.md`, and the generated `inventory.md` for live state). Read those rather than guessing.

## The machine

- Ubuntu 24.04, user `beastblaster` with passwordless `sudo`. Always on, **no UPS**; the BIOS powers
  back on after a power cut and everything is meant to come back by itself.
- The desktop is **real and in use**: GNOME on **X11**, display `:0`, GDM autologin. Sandesh reaches it
  over RustDesk, so anything you do on screen he may be watching or using at the same moment.
- Disks: the NVMe SSD holds the OS, Docker and apps. The 7.3 TB USB HDD `TM1`
  (`/media/beastblaster/TM1`) holds photos, Nextcloud data, the media library and Frigate recordings.
  Docker waits for TM1 to mount. Don't move, rename or "clean up" anything on TM1.
- Exposed to the internet only through Pangolin on the Hetzner VPS (every URL is behind SSO). Never
  open a port on the LAN or the internet to make something reachable; add a Pangolin resource instead
  (`aibo-server/infrastructure/pangolin.md`).

## Which tool

| Need | Use |
|---|---|
| A web page | **agent-browser** (MCP or CLI). Its own Chrome-for-Testing, headed on `:0`, profile `~/.agent-browser/profile` (stays logged in), downloads in `~/Downloads/agent-browser/` |
| A desktop app or window | **computer-use-linux** MCP. Call `get_app_state` first; use `list_windows` / `focused_window` before typing |
| Screenshot of a UI you changed | `ui-shot` (the `ui-check` skill) |
| Secrets | `fleet-secret get NAME` (source of truth `~/Documents/secrets/keys.env`). Never print a secret |
| Asking Sandesh for help | `agent-tools` MCP `notify` → his phone |

## Desktop rules (learned by breaking them)

- **Never install or register `cua-driver` here.** On this box it froze the visible desktop with its
  overlay and switched the Orca screen reader on. The Macs keep it; aibo-linux doesn't.
- Accessibility needs `org.gnome.desktop.interface toolkit-accessibility=true` **only**. Never set
  `screen-reader-enabled`. If Orca appears, check `pgrep -a cua-driver` first.
- computer-use-linux drives the **real** pointer and keyboard and steals focus while it acts. Keep
  sessions short and leave the windows as you found them.
- Only type into a window you opened fresh or have just verified. Apps restore their last session:
  gnome-text-editor once reopened a real file and test text landed in it.
- It refuses to type when it can't verify focus (e.g. a system modal is up). Look at the screen
  instead of retrying blindly.
- Chrome-for-Testing runs with its sandbox, made possible by `/etc/apparmor.d/agent-browser-chrome`.
  Never "fix" a browser crash with `--no-sandbox`: this browser visits arbitrary pages.

## Services

- **cptr** (the agent hub you are probably running inside): `systemctl --user` unit `cptr.service`,
  `127.0.0.1:8899`. **Restarting it kills every running agent turn, including yours.** Restart it
  only through `swap-when-idle.sh`, which waits until no chat is mid-reply:
  `systemd-run --user --unit cptr-restart --collect ~/src/computer/swap-when-idle.sh /usr/bin/systemctl --user restart cptr.service`
  (see `aibo-server/infrastructure/agents/cptr/README.md`).
- cptr reads the skill folders when it starts, so a newly linked skill only shows in cptr's own list
  after a restart (Claude Code and OpenCode pick it up on their next session).
- Docker stacks: compose files and how each starts are in `aibo-linux/docker.md`. Frigate always
  needs `--env-file ~/Documents/secrets/keys.env`. agent-tools (artifacts, ntfy, mcp-tools, memory)
  is its own compose in `~/Documents/personal/agent-tools/`.
- User timers worth knowing: `sync-ai-sessions.timer` (chat exports, every 15 min),
  `sync-repos.timer` (pulls/pushes the repos), `memory-export.timer` (01:30), `cptr-watchdog.timer`. Root's crontab runs the nightly `backup.sh`
  (02:30). `systemctl --user list-timers` shows the rest.
- Agent tools (MCP servers, skills, helper commands) come from `agent-tools/registry.toml`. Don't
  `claude mcp add` or hand-edit `opencode.json`: change the registry, then
  `~/Documents/personal/agent-tools/setup.sh --profile-from aibo-server --only wiring`.

## Housekeeping

- Repos live in `~/Documents/personal/<repo>` (same layout as aibo-mac). Several folders are **live
  paths** that units or cron run from (`aibo-server/docker/`, `aibo-server/infrastructure/scripts/`,
  `agent-tools/`): don't move or rename them.
- After changing how the machine is set up, update `aibo-linux/README.md` (and
  `infrastructure/scripts/inventory.sh --write` for the generated snapshot).
- Ask before anything that deletes data, stops a Docker stack, or reboots: Sandesh's photos, cameras
  and media run here.
