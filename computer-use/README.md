# computer-use

Lets agents (Claude Code, OpenCode, cptr) use a Mac the way a person does. It sets up the
same two tools as aibo-mac:

| Tool | Drives | How agents use it | Vendor |
|---|---|---|---|
| **cua-driver** | Desktop apps and anything outside a web page: windows, menus, dialogs, clicks, screenshots | MCP server (`cua-driver mcp`) + the `cua-driver` skill | [trycua/cua](https://github.com/trycua/cua), installed as `/Applications/CuaDriver.app` |
| **agent-browser** | Inside web pages: snapshot, click, type, download, tabs | CLI + the `agent-browser` skill (no MCP needed) | [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser), `npm i -g agent-browser` |

The rule on aibo-mac: web pages go through agent-browser, everything else through cua-driver.
The machine-specific rules (1Password logins, 2FA from Messages, the Agent Chrome) are in
`personal-agent/skills/mac-operator`, which is not part of this repo.

## Set up a new Mac

```bash
git clone https://github.com/sandeshkini/agent-tools.git && cd agent-tools/computer-use
./install.sh                  # the two tools, the cua-driver daemon, skills, MCP wiring
./install.sh --agent-chrome   # same, plus the Agent Chrome (recommended for routines)
cua-driver permissions grant  # the one step a person has to click through
./install.sh --check          # what's installed and running; changes nothing
```

Needs: macOS, Homebrew's `node`/`npm`, and Google Chrome if you use `--agent-chrome`. Claude
Code and OpenCode are optional; the script wires up whichever is installed. Run it again at
any time: it only adds what's missing.

## What the script does

1. **cua-driver**, through the vendor installer (`curl -fsSL https://cua.ai/driver/install.sh | bash`).
   You get `/Applications/CuaDriver.app`, which has a stable bundle id `com.trycua.driver`, so
   macOS permissions survive updates. The wrapper goes in `~/.local/bin/cua-driver`.
2. **The cua-driver daemon**, LaunchAgent `com.trycua.cua-driver`, running `cua-driver serve`
   with KeepAlive. The vendor installer doesn't register one on macOS. Without it, macOS
   attributes permissions to whichever terminal started the tool. The script writes the plist
   itself, the same way the other installers in this repo do.
3. **Skills.** `cua-driver skills install` links the vendor's skill into every agent it finds.
   The agent-browser skill ships inside the npm package, and the script links it into
   `~/.claude/skills`, `~/.config/opencode/skills` and `~/.agents/skills`. Folders that are
   already there are never replaced.
4. **MCP.** `claude mcp add --scope user cua-driver`, and a `cua-driver` entry in OpenCode's
   config. If the OpenCode file has comments, the script prints the snippet to add instead of
   rewriting the file.
5. **Browser.**
   - **With `--agent-chrome`:** LaunchAgent `com.sandesh.agent-chrome` runs a visible Chrome
     with its own profile (`~/Library/Application Support/Google/Chrome-Agent`) and debugging
     on **:9333**. `~/.agent-browser/config.json` gets `{"cdp": "9333"}`, so agent-browser
     attaches to that Chrome instead of opening its own. Chrome only asks "Allow remote
     debugging?" for the default profile, so this one never needs a click. It also links the
     1Password native-messaging host, so the extension uses the desktop app.
   - **Without it:** `agent-browser install` downloads Chrome for Testing, and each session
     launches its own headless browser.

## Still manual

- `cua-driver permissions grant`: Accessibility and Screen Recording. macOS only allows a
  person to grant these.
- Logins in the Agent Chrome: Google, the 1Password extension, and the sites the routines use.
  The profile is local to each machine and never synced.
- Restart the agents so they load the new MCP server and skills.

## Updating

```bash
cua-driver update --apply     # keeps permissions (same bundle id)
cua-driver skills update
npm i -g agent-browser
```

## Per machine

| Machine | Status |
|---|---|
| aibo-mac | Installed (cua-driver 0.28.2, agent-browser 0.38.1, Agent Chrome on :9333). The script was written from this setup |
| aibo-dev, artos-agent | Not installed yet |

## Linux: use something else

Don't install cua-driver on Linux. aibo-linux tried it and removed it on 2026-09-25: among
other problems it switched on the Orca screen reader. Linux machines use
**computer-use-linux** (desktop, through AT-SPI) plus **agent-browser** (headed Chrome for
Testing, sandboxed by an AppArmor profile). That setup is written up in aibo-server:
`infrastructure/agents/cptr/README.md` § "Desktop + browser control". This script refuses to
run on anything but macOS.

The older, longer cua-driver notes (permission modes, `--grant existing-profile` being
launch-time only, the aibo-dev setup) were removed in commit `00dccac`. Read them with
`git show 00dccac^:cua-driver/README.md`.
