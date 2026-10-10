---
name: mac-operator
description: Operate Sandesh's Mac (aibo-mac) and the Agent Chrome for routines — logging into sites with 1Password, reading 2FA codes from Messages, driving web apps (incl. Google Sheets) and desktop apps. Use for any browser or desktop task on this machine, and before running any routine in personal-agent/routines. Web pages go through agent-browser; desktop apps and anything outside the page go through cua-driver. This skill adds machine-specific facts and hard-won rules on top of those two skills.
---

# Operating aibo-mac

Paths below that start with `personal-agent/` are in `~/Documents/personal/personal-agent/`.
This skill lives in agent-tools (`skills/mac-operator`, aibo-mac only in `registry.toml`).

Sandesh's preferences and rules (read first): `personal-agent/docs/operating-manual/how-sandesh-works.md`.

Human-facing map of the whole stack (services, setup, troubleshooting):
`personal-agent/systems/computer/README.md`.

Load the **`agent-browser`** skill (web pages) and the **`cua-driver`** skill
(desktop apps). This file doesn't repeat them. It records what is specific to
this machine and what went wrong when these rules were skipped.

## The rule

Work like a person at this Mac: through the screen, in the Agent Chrome
(his logins, his 1Password) and real apps. Do not replace on-screen steps with APIs or direct
access to an app's data. Add scripts wherever they help: parsing downloads,
working out rows, checking totals. Computer work first, helped by scripts,
never scripts instead.

## Which tool

| Need | Use |
|---|---|
| Anything inside a web page, in the Agent Chrome | **agent-browser** (CLI): `snapshot -i`, `click @eN`, `press`, `keyboard type`, `download`, `screenshot`, `tab …` |
| Things drawn outside the page: 1Password inline menu, Chrome permission dialogs | cua-driver native `get_window_state` + pixel `click` |
| Native Mac apps (Messages, 1Password app, System Settings) | cua-driver native tools |
| Secrets | `pa-secret get NAME` (= fleet-secret: keys.env, Keychain fallback). Never print a secret |
| Asking Sandesh for help | `agent-tools` MCP `notify` → his phone |

Do **not** use cua-driver's browser tools (`browser_*`, `get_browser_state`) or
the Playwright MCP any more. cua can only fake clicks inside Chrome on macOS
(Sheets ignores them), and Playwright's downloads are broken.

## The Agent Chrome (default for all web work)

Routines run in the **Agent Chrome**: a separate, visible Chrome window with its own
profile ("Agent"), data dir `~/Library/Application Support/Google/Chrome-Agent`.
- **Started by LaunchAgent** `com.sandesh.agent-chrome`, with RunAtLoad and KeepAlive.
  It reopens after quit, reboot or power cut, with debugging on port **9333**.
  Chrome only prompts "Allow remote debugging?" for the *default* profile, so this
  one never needs a human click.
- **Use the default agent-browser session.** agent-browser's own guide says to always name a
  session; on this Mac that's wrong — the default session is the one attached to the Agent Chrome.
- **agent-browser config:** `~/.agent-browser/config.json` = `{"cdp": "9333"}`, so the
  default session always attaches here and never launches a browser of its own.
  Check with `agent-browser get cdp-url` (should show `127.0.0.1:9333`).
- **1Password:** the extension is installed. It talks to the desktop app because
  `Chrome-Agent/NativeMessagingHosts/com.1password.1password.json` is linked to the
  main Chrome's copy. A custom `--user-data-dir` doesn't see the default one, so
  without the link the extension asks for a standalone 1Password sign-in.
- **Logged in (2026-09-25):**
  - Google sandesh1993@gmail.com: passkey via 1Password "Google - Main". Not the Filemonk entry.
  - Wealthsimple, Amazon.ca, Borderless.
- **Sandesh can watch and step in:** it's a normal window on screen.
- **Don't use his own Chrome** unless he asks. His Chrome (port 9222, default
  profile) still has "Allow remote debugging" on, but connecting there shows a
  dialog only he can click, once per Chrome restart.
- **If the Agent Chrome isn't reachable:**
  `launchctl kickstart -k gui/$(id -u)/com.sandesh.agent-chrome`.
  Then `agent-browser tab list`.
- **Native Chrome dialogs** (extension install, passkey pickers) belong to this
  Chrome's own process (`pgrep -f Chrome-Agent`). Screenshot the *main* window
  (`get_window_state`) to see them. Click the dialog window at (main-window coords
  + main origin − dialog origin): AX presses on dialog buttons are refused.

## Logging in with 1Password

- The 1Password app must be unlocked (auto-lock is set to Never, but it **locks
  after every restart**, e.g. the 2026-09-25 power cut). The in-page button then
  reads "Unlock 1Password to sign in". Only Sandesh can unlock it (master password
  or Touch ID), so notify him. Check for a
  window titled "All Accounts — All Items — 1Password".
- **Try the page first.** On Amazon and Wealthsimple, focusing the email field
  makes 1Password show an **in-page** button, "Sign in with <site> — <email>".
  It's in `snapshot -i`, so `agent-browser click` it and 1Password fills and
  submits. You never see the password.
- **Passkey offered? Use the saved password instead** (Sandesh's rule: logins go
  through 1Password only, and runs use the password, not the passkey). If clicking the
  button shows "Complete passkey sign in — Enter your PIN or biometric input", the item
  is a passkey and only Sandesh can finish it. Click the panel's **"Other logins"**
  button instead and pick the item **without** the person-and-key icon (screenshot to
  tell them apart). Wealthsimple: `Wealthsimple, sandesh1993@gmail.com` is the password,
  `wealthsimple.com, …` is the passkey.
- Only if there's no such button: the classic inline 1Password menu is drawn
  **outside the page**. Use cua-driver: `get_window_state` of the Chrome
  window, pixel-click the 1Password icon in the email field, then the account row.
- Wealthsimple opens a native Chrome dialog, "Use a saved passkey for
  my.wealthsimple.com" (its own window). Cancel it with cua-driver
  (`list_windows`, then click its Cancel) before using the page.

## 2FA codes by SMS

- Codes arrive in **Messages** (iMessage is signed in here). The Messages
  database is blocked by macOS privacy; read the app with cua-driver.
- If Messages has no window, `launch_app` (bundle `com.apple.MobileSMS`) opens one;
  then `list_windows` and use the window taller than 100 px.
- **Use `personal-agent/bin/pa-sms-code <site>`** instead of reading by hand. It
  notes the codes already showing, polls Messages through cua every 10 s for up
  to 3 min, and prints only a **new** code (exit 1 if none). **Start it in the
  background *before* you trigger the code** (before clicking Continue/Send), so
  a fast code isn't counted as old. Sites: `amazon`, `wealthsimple`,
  `opentable`, or pass a regex. `--any-age` takes the newest existing code.
- **Codes can take 2–3 minutes to arrive.** Poll the conversation list every 15 s
  for up to 3 minutes before deciding it isn't coming. Look for a new top row
  with the site's name and a time after you requested it. Amazon sends from
  **78003** ("Amazon: Your code is …"). On 2026-09-25 I gave up after about a
  minute; the code arrived right after and Sandesh had to point it out.
- On resend the code may come from a **different number** (Wealthsimple: first
  28849, then +1 888 970-3846). Use the newest message with the site's wording,
  within a couple of minutes. Stale codes fail.
- Tick "don't ask for a code for 30 days" when offered.
- Stop after two failed codes (the account can lock). Notify Sandesh.

## Google Sheets with agent-browser (tested 2026-09-25)

The grid is a canvas, but the **Name box** (`textbox` showing the current cell,
e.g. `A1`) and all **menus** (`menuitem "Edit"`, `"Cut t ⌘X"`, …) are in `snapshot -i`.

- **Go to a cell or range:** click the Name box ref, `press Meta+a`,
  `keyboard type "A418"` (or `"B3:D12"`), `press Enter`, then wait about 1 s.
- **Typing a value:** `keyboard type` only lands if the cell editor is open.
  **Press Enter first to open the cell**, then type. (F2 does nothing on this Mac.)
- **Entering rows**, as a person would. For each cell: `press Enter`
  (open), `keyboard type "<value>"`, then `press Tab` to save and move right. After
  the row's last cell, `press Enter`: it saves and drops to the next row, back
  in the starting column. Two 3-cell rows take about 1 s. Dates typed as
  `2026-09-14` become real dates.
- **Helper:** `personal-agent/bin/pa-sheet-type START < rows.tsv` types rows.
  `--replace` overwrites filled cells (it presses Delete first; typing into a
  filled cell would append). `--goto RANGE` only selects.
- **After any reload or export,** the first edit can land on the previously selected
  cell even though the Name box shows the target: "electronics" went to D434
  instead of G366. Always verify with a fresh xlsx download and a
  before/after diff of the tab. Expect only the cells you meant to change.
- **Clearing:** select the range via the Name box, then `press Delete`.
- **Paste does not work:** `press Meta+v` and `clipboard paste` do nothing in
  Sheets. `clipboard write` does set the Mac clipboard, but Sheets ignores the
  paste. So don't plan on copy/cut/paste. Type rows in, and clear old cells.
- **Formatting:** the menus work (Format ▸ Number ▸ …). Typed cells take the
  cell's existing format, so new rows under old ones usually match already.
  Check in a screenshot.
- **Read / verify:** open `…/export?format=xlsx` in the tab. It lands in
  `~/Downloads`. **Copy** it to /tmp and read it with openpyxl (`uv run --with openpyxl`). Don't delete or move it (see Downloads below). Trust the download, not the screenshot.
- **Scratch tests:** `tab new https://sheets.new`, then File ▸ Move to trash
  when done.

## Utility bills (running costs; tested 2026-09-27/28)

File each bill under the **month the money leaves the account** (Hydro bills in one month and
is paid the next; the others bill and pay in the same month). Read-only: never Pay, never
change billing or payment settings.

- **Enbridge** (myaccount.enbridgegas.com): 1Password's in-page button signs in. Then two
  prompts: **Skip MFA** (don't enable it) and "Stay safe" → Ok. A cookie banner blocks clicks
  until dismissed. The login has **two accounts** and opens on the old Etobicoke one: pick
  **1095 OLD OAK DR OAKVILLE** from the address list. Account Activity → Bills → Export CSV gives
  a year of bill amounts (UTF-16, `iconv -f UTF-16`).
- **Cogeco** (cogeco.ca → My Account → Manage residential services): 1Password button
  "Sign in with Cogeco — beastblaster". Close the "web shortcut" modal. Billing lists dates
  only; each bill's **Download** gives a PDF (`pdftotext -layout`, "Current month subtotal").
  Bills include a $40/month 24-month promo; flag when it disappears.
- **Oakville Hydro** (myaccount.oakvillehydro.com): **Sign in with Google** as
  sandesh1993@gmail.com (1Password item "OakvilleHydro", signs in with "Google - Main"). View
  Billing History shows bill date, period, amount and due date as text.
- **Fido, Reliance**: the bill emails carry the amount.
- `agent-browser download` waits for its own filename and can time out while the file has
  already landed (under a random name) — run it in the background and check the folder.

## Gmail in the Agent Chrome (reading)

- Email rows show up in the full `snapshot`, not in `snapshot -i`.
- Keyboard `k`/`j` doesn't step between open emails; use the **Newer** / **Older** buttons.
- Opening an unread email marks it read. Put it back (Mark as unread) unless the task was to read it.

## LifeOS in the Agent Chrome

Open **`http://127.0.0.1:4011/lifeos/?tab=<tab>`** (lifeos-web, local only, no SSO, reads and
writes). Not `http://127.0.0.1:4000/`: LifeOS is built for the `/lifeos` path and renders
blank there. `bin/pa-shot lifeos:<tab>` is for screenshots only (read-only proxy).

## 1Password in the Agent Chrome (extra)

- `cmd+shift+x` (cua `hotkey`, foreground, on the Agent Chrome window) opens the extension
  popup: search item titles to see whether a login exists. No results means no item.
- After a "Sign in with Google" login, 1Password shows an in-page **"Save in 1Password?"**
  banner (in `snapshot -i`): Save item → check the vault (Personal, not Rocketmonk) → Save.

## Amazon.ca

- Sign-in: the page shows 1Password's own button ("Sign in with Amazon.ca —
  sandesh1993@gmail.com"), which is a page element, so `agent-browser click`
  works. Then Amazon texts a code from 78003.
- Never take the "try a different way" route: it asks for a card's CVV.
- **Match card charges** at `https://www.amazon.ca/cpe/yourpayments/transactions`.
  It lists every charge and refund with date, card (Wealthsimple Visa ****0166,
  other Visa ****1030) and order number. Amazon charges per shipment, so one
  order can be several charges. Items: `…/gp/your-account/order-details?orderID=<n>`
  (`agent-browser read`).

## iPhone (iPhone Mirroring)

Set up 2026-09-25: the "iPhone Mirroring" app (`com.apple.ScreenContinuity`)
mirrors Sandesh's iPhone (iPhone 18 Pro Max, iOS 27). The phone must be within
about 10 m with Wi-Fi and Bluetooth on, and locked. Notification forwarding to the
Mac was declined on purpose.
- **Only when a routine or Sandesh explicitly names a phone step** (a phone-only
  app, approving an "Is this you?" prompt, reading a code that doesn't reach the
  Mac). Never browse the phone, open messages, photos or banking apps unless told
  to. Read-only unless the step says otherwise.
- Drive it with cua-driver **pixels**: it's a video-like window. `get_window_state`
  with a screenshot, then `click` x,y with `delivery_mode:"foreground"`; type with
  the Mac keyboard. Top-left back chevron is about (40,102) on the 354×781 window.
- If it shows "iPhone Mirroring Is Locked" (Mac password) or "Unlock iPhone",
  notify Sandesh; only he can do that.
- Go back to where you started when done.

## Screen lock

`personal-agent/bin/pa-lockcheck` exits 1 when locked. Locked means cua-driver
desktop clicks and keys fail (1Password pop-ups, Messages, Chrome dialogs).
agent-browser input goes straight into Chrome and should still work, but that's
untested.

**Why it locked (found 2026-09-25 in the loginwindow log):** the idle
**screensaver**. After 1200 s with no real keyboard or mouse input, loginwindow
starts the screensaver, and with password delay 0 that locks at once. Agent
input doesn't count as user activity. A connected RustDesk session hid this,
because RustDesk blocks display sleep while connected. The lock came when a
session dropped, and also 20 min after the power-cut reboot.

**Fix in place:**
- LaunchAgent `com.sandesh.keepawake` runs `caffeinate -d -i` forever. loginwindow
  skips the idle screensaver while display sleep is blocked.
- Screensaver idleTime is set to 0.
- `com.sandesh.pa.lock-watch` (`bin/pa-lockwatch`) logs every lock and unlock with
  loginwindow's stated reason to `var/lockwatch.log`, and pings the phone on lock.
- A reboot auto-logs in (autoLoginUser=sandesh) without locking.

If it still locks, read `var/lockwatch.log` first.
- Use `/usr/bin/log`, not `log`: in zsh, `log` is a builtin and silently returns nothing.

## Downloads: copy, never move or delete (found 2026-10-05)

Files the Agent Chrome downloads carry a `com.apple.macl` tag that ties them to Chrome. When an
agent process tries to **delete, move, or touch the xattrs** of such a file (`rm`, `mv`, `xattr`),
macOS wants to ask for "access data from other apps", but agent processes can't show that
prompt, so the command **hangs forever** with no error. This froze a whole chat on 2026-10-04.
- Reading and `cp` work. Copy downloads to /tmp and work on the copy.
- Chrome names repeats `Name (1).xlsx`, `Name (2).xlsx`: always take the newest (`ls -t`).
- Wrap any other command on ~/Downloads in a time limit: `perl -e 'alarm 10; exec @ARGV' <cmd>`.
- Sandesh clears ~/Downloads in Finder now and then.

## Changing how a background service starts (learned 2026-10-06)

macOS ties privacy grants (Full Disk Access, Documents, iCloud) to the **app identity** macOS sees: a
named app (`pa-app`), or the bare interpreter if it runs `python script`. Wrapping a service in an
app, renaming it, or rebuilding it with a new signature gives it a **new identity with no grants**.
On 2026-10-06 cptr was switched to `cptr.app`: it started fine, then froze on the first protected
file, the watchdog's restarts collided with the frozen copy on port 8000, and Sandesh had to fix it
over RustDesk.
- Grant the new identity's permissions **before** switching (have Sandesh add the app in System
  Settings first), then switch.
- A health check must exercise real work (read a protected folder through the service), not just
  "process started / port answers"; roll back if that fails.
- Never switch cptr itself without Sandesh at the Mac.
- Tools for it: `agent-tools/setup.sh --profile-from aibo-server --check` reports every machine-level
  service against `aibo-server/machines/<machine>/setup.toml` and changes nothing. Its `cptr-app` step
  (mac-apps) does the safe order above. `bin/pa-app` builds new named apps through agent-tools/mac-apps.
  Never rebuild an existing app just to "refresh" it: an ad-hoc rebuild drops its privacy grants.

## Housekeeping rules (learned by breaking them)

- Never use `osascript` to change apps or `open -a` to launch. Use cua's
  `launch_app` and the typed tools.
- Do not rerun a failing step in a loop. Each rerun opened another tab, another
  passkey dialog or another "Allow remote debugging?" dialog. Look at the
  state, then decide.
- The first cua screenshot right after an unlock can be a half-drawn frame.
  Take a second one before trusting coordinates.
- cua-driver sessions expire. If a call says the session ended,
  `start_session` again.
- Close every tab you opened. Leave Chrome as you found it.
- Read-only on financial sites: never click pay, transfer, move money, or
  change settings.
- When stuck on anything only a human can do, `notify` Sandesh with what you
  need, then wait.
