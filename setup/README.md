# setup

One command that brings a machine (Mac or Linux) to its **profile**: which agent tools it runs, and
how. It reuses the installers in this repo; it doesn't replace them.

```bash
git clone https://github.com/sandeshkini/agent-tools.git
agent-tools/setup.sh --check      # what's in place vs the profile. Changes nothing
agent-tools/setup.sh              # do what --check said would change (asks first)
```

The same two commands work for a new machine and an existing one. On an existing machine, `--check`
should say "in place" for everything, or list the real gaps.

## What it manages

| Component | macOS | Linux | Done by |
|---|---|---|---|
| `computer-use` | cua-driver + daemon, agent-browser, skills, MCP wiring, optional Agent Chrome | not applicable | `computer-use/install.sh` |
| `mcp-tools` | LaunchAgent on 127.0.0.1:8009 | the Docker service (`mode = "docker"`): checked only | `mcp-tools/install.sh` |
| `stuck-watch` | LaunchAgent | not applicable | `stuck-watch/install.sh` |
| `cptr-watchdog` | LaunchAgent, pointed at the profile's cptr label/port | systemd `--user` timer; another port goes in a drop-in | `cptr-watchdog/install.sh` |
| `sync-ai-sessions` | LaunchAgent (needs the ai-memory repo and `uv`) | timer: checked; set up by the profile's `installer` if given | `sync-ai-sessions/install.sh` |
| `cptr` | report only: registered and answering, which version | same, for the systemd unit | nothing: setup never installs or restarts cptr |
| `named-apps` | runs the other jobs as named apps (Stuck Watch, the watchdog; optionally session sync and mcp-tools) | not applicable | `mac-apps/install.sh --<job>` |
| `cptr-app` | runs cptr as `cptr.app` with its own Full Disk Access | not applicable | `mac-apps/install.sh --cptr` |

A job that already does the work counts as in place, whoever made it: a named app built by mac-apps
or Personal Agent's `pa-app`, or a hand-made LaunchAgent. Setup doesn't rebuild or replace it.
Rebuilding a named app changes its ad-hoc signature, and macOS then forgets its privacy grants.

Components run in the table's order. Later ones see what earlier ones installed, so one run can
install Stuck Watch and then wrap it in a named app. `cptr-app` always runs last.

## Commands

```bash
setup.sh --check                  # report only (exit 0 = all in place, 3 = something to do, 1 = error)
setup.sh   (or --apply)           # apply; on a terminal it shows the plan and asks once
setup.sh --only stuck-watch,cptr-watchdog [--check]
setup.sh --rollback named-apps    # undo the last apply of one component
setup.sh --list                   # components, and which the profile turns on
setup.sh --profile-from my-infra --check        # see "Profiles"
--yes      don't ask (also needed for cptr-app when there's no terminal)
--notify   push the --check result via ntfy too (apply and rollback always push)
```

`--check` shows each component as **in place**, **would change** (with the steps), **needs a person**
(with what to do) or **skipped** (turned off, or not for this OS).

## Profiles

A profile is a small TOML file: the components to turn on, plus per-machine settings (cptr's label,
port and install method, where named apps go, a secrets file). Start from
[`profiles/example.toml`](profiles/example.toml), which documents every key.

Setup uses the first of these it finds:

1. `--profile <file>`, or `--profile-from <repo or folder>`
2. `$AGENT_SETUP_PROFILE`
3. `~/.config/agent-setup/profile.toml`

Keep real profiles out of this public repo: they name machines and paths. Put them in a private repo
as `machines/<machine>/setup.toml`. Then `--profile-from <that repo>` picks the profile whose `[match]`
fits this host. Every key given must match, and a value can be a list:

| `[match]` key | Compared with |
|---|---|
| `computer_name` | macOS ComputerName (System Settings > General > About). Curly and straight apostrophes count as the same |
| `local_hostname` | macOS LocalHostName |
| `hostname` | `hostname`, with or without the domain |
| `model` | `sysctl -n hw.model` on a Mac (e.g. `Mac14,12`), the DMI product name on Linux |
| `os` | `darwin` or `linux` |

`--profile-from my-infra` looks for a folder of that name next to this clone and in `~/Documents`,
`~`, `~/src` and `~/code`. A path works too. Two Macs can share a
short hostname, so match those on `computer_name` + `model`. To skip the flag later, link the file:
`ln -s <repo>/machines/<machine>/setup.toml ~/.config/agent-setup/profile.toml`.

## What needs a person

Setup stops and says so in these cases. It never tries to get around them:

- **Full Disk Access for `cptr.app`.** `cptr-app` builds the app, opens System Settings and waits
  (up to 10 min) until a probe running as the app can read a Full-Disk-Access-only file. Only then
  does it switch cptr over. You add the app and confirm with your password or Touch ID.
- **Accessibility and Screen Recording for cua-driver** after a fresh install:
  `cua-driver permissions grant`.
- **First-run prompts** of a new named app (e.g. "Stuck Watch would like to access Documents").
  Click Allow.
- **Secrets.** mcp-tools needs `PUBLISH_TOKEN` and `NTFY_TOKEN`. Export them, or set
  `[secrets] env_file` to a `KEY=VALUE` file (chmod 600). Setup passes them to the installer and never
  prints or logs them. Its output and log redact anything that looks like a token.
- **Logins** in the Agent Chrome, and cloning the ai-memory repo for session sync.

`cptr-app` restarts cptr. It only runs from a terminal, or with `--yes`. Run it at the Mac, from
Terminal.app rather than from inside cptr. From inside cptr it still works, but your session drops
at the restart: the switch is detached and finishes on its own. The result goes to
`~/Library/Logs/mac-apps.log` and ntfy.

## Safety and rollback

The cptr switch follows the cptr fork's swap script (`swap-to-fork.sh`): it runs detached, is
health-gated, and rolls back on its own. Step by step: build `cptr.app` → Full Disk Access granted
and proven by a probe as the app → back up the plist → restart detached, with the watchdog paused
→ health gate (HTTP answers, a protected-path probe as the app passes, cptr is still up ~15 s later)
→ otherwise restore the old plist and restart. Per-host differences (label, port, install method,
a `zsh -c 'source keys.env && exec cptr …'` wrapper) come from the profile or from detection, the
way the swap script takes env overrides per host.

For every other component, setup first copies the files its installer may write (plists, systemd
units, configs) to `~/.local/state/agent-setup/backups/<component>/<time>/`. After applying, it
detects again. If the component isn't in place, setup restores the copy and reloads the job.
`setup.sh --rollback <component>` does the same on request. It deletes files that didn't exist
before, and it doesn't uninstall binaries such as `CuaDriver.app` or the npm package.
`named-apps` and `cptr-app` roll back through mac-apps' own plist backups.

Log: `~/Library/Logs/agent-setup.log` (macOS) or `~/.local/state/agent-setup/agent-setup.log`
(Linux). Apply and rollback results are pushed via ntfy. Setup finds the ntfy settings the way
Stuck Watch does: env, the profile's `[ntfy]` / secrets file, `~/.config/stuck-watch/config`, an
installed mcp-tools LaunchAgent, this clone's `.env`.

## Adding a component

1. Create `agentsetup/components/<name>.py` with a `Component` subclass. Set `name`, `description`,
   `platforms` and `order`, and add `not_applicable()` if the reason it doesn't apply needs
   explaining. Then implement:
   - `detect()`: read the machine and return plain data (dicts, strings, bools). No changes.
   - `plan(facts)`: pure. Facts + `self.cfg` (its profile table) → `Plan(OK | CHANGE | GAP, summary,
     actions, human, notes, data)`.
   - `apply(plan)`: make the change, usually by running an installer through
     `self.ctx.run([...])`, which streams redacted output to the terminal and log. Return True/False.
   - `owned_files()`: the files apply may write. That's enough for automatic rollback. Override
     `rollback()` only if undoing needs more.
2. Register it in `agentsetup/components/__init__.py`.
3. Add tests to `tests/test_setup.py` that feed `plan()` fake facts, then run
   `python3 setup/tests/test_setup.py`.
4. Document it in this README's table and in `profiles/example.toml`.

Python 3.9+ standard library only, so `/usr/bin/python3` from the Command Line Tools is enough. The
TOML reader falls back to a small built-in parser where `tomllib` is missing.
