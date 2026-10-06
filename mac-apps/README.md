# mac-apps

Runs **cptr**, the **cptr watchdog** and **Stuck Watch** as properly named macOS apps, on any Mac.

## Why

A LaunchAgent that runs `python3 script.py` or `bash watchdog.sh` shows up in System Settings ›
Privacy & Security (Full Disk Access, Files & Folders, Automation…), in privacy prompts and in
Activity Monitor as a bare **python3.12** or **bash**. That causes two problems:

- **Naming.** You can't tell which job a "python3.13 would like to access…" prompt is for, or which
  entry in the Full Disk Access list belongs to what.
- **Grant ownership.** A grant to `python3.13` covers *every* script that interpreter ever runs.
  Give cptr Full Disk Access that way and any other job on the same Python gets it too.

mac-apps wraps each job in a tiny app (`~/Applications/Agent Apps/<Name>.app`, or
`~/Applications/Personal Agent/` if that folder already exists). Inside is a ~60-line compiled
launcher that starts the job's original command as a child and waits. launchd starts the launcher,
so macOS treats the app as the *responsible process*: prompts say "cptr would like to…", the
lists show **cptr**, **cptr Watchdog**, **Stuck Watch**, and cptr's Full Disk Access belongs to
`cptr.app` alone.

## The lesson this is built around

Wrapping a service gives it a **new app identity with no grants**. The first time this was done by
hand (cptr switched to `cptr.app`), cptr started and passed a "port answers" check, then froze on a
Full Disk Access prompt nobody could see. Then the watchdog's restarts collided with the frozen copy
on the port, and nothing rolled back. So `--cptr` does it in this order:

1. builds `cptr.app` (the original command unchanged, including `zsh -c 'source keys.env && exec cptr …'` wrappers)
2. opens System Settings › Privacy & Security › Full Disk Access and tells you exactly what to click
3. **waits until a probe running as the app can read a Full-Disk-Access-only file** (the system
   `TCC.db`, and `~/Library/Mail` if it exists). It checks every 5 s for up to 10 min and won't go
   on without that
4. backs up the plist and switches only `ProgramArguments` to the launcher (label, environment,
   working folder, logs and KeepAlive are kept)
5. restarts cptr **detached** (if you run this from inside cptr, your session drops, and the switch
   finishes on its own). It pauses the watchdog during the switch, and makes sure no stale cptr
   still holds the port
6. **health gate** (60 s): launchd runs the launcher, cptr answers HTTP on its detected port, a probe
   as the app reads the protected file, and cptr keeps answering for ~15 s more (the incident's
   copy started and *then* froze). If any check fails, it restores the old plist and restarts
7. writes the result to `~/Library/Logs/mac-apps.log` and pushes it via ntfy if configured (same
   discovery as Stuck Watch: `NTFY_*` env, `~/.config/stuck-watch/config`, or an installed
   mcp-tools LaunchAgent)

The watchdog and Stuck Watch need no Full Disk Access. They're wrapped and reloaded the same way
(with rollback if the job doesn't run). The first time, the new app may ask once for something like
Documents. Click Allow.

At the end it lists **stale privacy entries**: grants still held by `python3.x` / `bash` / `zsh`.
For each one it says whether a job now runs as a named app instead (stale), whether another
LaunchAgent still runs that interpreter (keep), and how to remove it. The list needs a shell with
Full Disk Access to read `TCC.db`. Without it, you get the names to look for.

## Run it (each Mac, at the Mac or over RustDesk)

```bash
cd <your agent-tools clone> && git pull && mac-apps/install.sh --check
mac-apps/install.sh --all        # then this, from Terminal, with someone able to click System Settings
```

Run it from Terminal.app rather than from inside cptr, so you see the outcome. From inside cptr it
still works, but your session ends at the restart. The result goes to the log and to ntfy.

| Mode | Does |
|---|---|
| `--check` (default) | finds cptr's LaunchAgent (any label whose command runs `cptr`), with its command, port (from what it listens on, `--port`, or `CPTR_PORT`), env names, working folder and logs. Also finds the watchdog and Stuck Watch jobs. Says "already named" or what would change, checks cptr.app's Full Disk Access and lists stale grants. **Changes nothing** |
| `--cptr` | the flow above |
| `--watchdog`, `--stuck-watch`, `--sync-ai-sessions`, `--mcp-tools` | wrap that job |
| `--all` | Stuck Watch, watchdog, then cptr last |
| `--rollback [KIND…]` | restores the newest backup (`~/Library/LaunchAgents/.mac-apps-backup/`) of each job (or just `cptr`, `watchdog`, `stuck-watch`, `sync-ai-sessions`, `mcp-tools`) that currently runs a mac-apps launcher, and restarts it (cptr detached, with the watchdog paused). The apps themselves are left in place |
| `build "<Name>" [--bundle-id ID] [--rebuild] -- <cmd…>` | build any named app whose launcher runs `cmd…` (an identical existing build is kept unless `--rebuild`) |
| `probe "<Name>" <path…>` | open paths as that app (through launchd, so macOS attributes it to the app, not your terminal) |

Jobs already wrapped (by mac-apps, or by Personal Agent's `pa-app`) are reported as "already named"
and left alone. Rebuilding an unchanged app is skipped, so its grants survive re-runs. The Stuck Watch
and watchdog installers also leave a plist alone once it runs a named app.

`agent-tools/setup.sh` runs these steps per machine profile (components `named-apps` and `cptr-app`).

Overrides (env): `MAC_APPS_DIR`, `MAC_APPS_BUNDLE_PREFIX` (default `local.agent-apps`),
`MAC_APPS_SIGN_IDENTITY` (a code-signing identity in your Keychain; default ad-hoc; used only if
it signs within 30 s),
`MAC_APPS_CPTR_LABEL` (if more than one job runs cptr), `MAC_APPS_CPTR_PORT`.

## Building and signing (shared with pa-app)

`build_app()` in `mac_apps.py` is the one named-app builder on every Mac. Personal Agent's `pa-app`
imports it for its own apps; it keeps its own folder, bundle ids and `pa-app.json`. For each app it:

- compiles the launcher (below) through `xcrun cc`, writes `Info.plist` and `Contents/Resources/mac-apps.json`
- signs with `MAC_APPS_SIGN_IDENTITY` (or the caller's identity) if that works within 30 s. A stable
  local identity keeps privacy grants across rebuilds. Otherwise (missing identity, or a Keychain
  prompt nobody answers) it signs ad-hoc, after deleting any `*.cstemp` a killed codesign left behind.
  Sealing that leftover and then losing it breaks the seal ("a sealed resource is missing")
- verifies the result with `codesign --verify --deep --strict`
- builds in a temporary folder beside the app and swaps it in only once the signature verifies.
  A failed build never leaves a half-built or broken-seal app, and the old app stays as it was

An app with `mac-apps.json` has this launcher, so it understands `--probe`. Apps built by older pa-app
versions don't. They're reported as named apps but never probed, and nothing rebuilds them unasked.

## The launcher

`Contents/MacOS/<Name>` runs the baked-in argv plus any extra arguments, via `posix_spawn`. It
forwards SIGTERM/SIGINT/SIGHUP to the child and waits for it. It exits with the child's status
(128+signal if the child was killed). `<launcher> --probe <path…>` lists each directory or reads
each file and exits 0 only if all of them opened. What it runs is recorded in
`Contents/Resources/mac-apps.json` (mode 600, since a command line can carry a secret).

## Limits

- **macOS only.** It needs the **Xcode Command Line Tools** (`cc` to compile the launcher, plus
  their `python3`). If they're missing, `install.sh` tells you to run `xcode-select --install`.
- **It needs a human.** Granting Full Disk Access needs someone at the Mac (password / Touch ID).
  Nothing can grant it remotely, and `--cptr` won't switch without it.
- **Ad-hoc signing ties grants to the exact build.** If an app is rebuilt (a changed command, a moved
  interpreter), macOS treats it as a new app and you grant it again. A stable
  `MAC_APPS_SIGN_IDENTITY` avoids that.
- The Full Disk Access probe runs as a separate launchd job using the same app. It has the same
  identity, so macOS gives it the same answer, but it's a separate process from the running cptr.
  The ~15 s check that cptr keeps answering covers the running process.
- Stale-grant removal is manual, through System Settings. It doesn't use `tccutil`, because
  `tccutil reset` can only wipe a service for all apps, or for a bundle id, not for a bare
  interpreter path.
