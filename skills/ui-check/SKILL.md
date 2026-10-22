---
name: ui-check
description: >-
  Look at the UI you just built or changed, the way Sandesh will see it, before saying it's done. Use whenever you change a web app's frontend (LifeOS, dashboards, mockups, artifacts): screenshot it at desktop and phone sizes, in light and dark, read the images, and judge them against the app's design system. Uses ui-shot (agent-browser in the Agent Chrome).
---

# UI check: look before you say "done"

A build that compiles isn't a UI that works. Every visible change gets looked at.

## The command

```
ui-shot <url> [--sizes desktop,phone,tablet,wide] [--themes light,dark] [--click "<tab text>"]
```

- For a cos mini app use its hub address, e.g. `http://127.0.0.1:4300/?space=daily:habits` (cos is where LifeOS's pages live now; LifeOS the app was retired 2026-10-13). `cos app preview` shows an app before install.
- `--click "Markets"` clicks a visible button or tab after load, for sub-tabs.
- It prints PNG paths (`/tmp/ui-shot/<name>-<size>-<theme>.png`). **Open them with Read and
  actually look.**
- Shots are shrunk to at most 800 px wide (phone shots are 440 px, device scale 1) and are the viewport only
  (one screen), so each costs ~0.5k tokens instead of ~1.7k, and it stays in the chat for every later turn.
  `--page` captures the whole page (height capped at 2400 px); `--full` keeps full pixel size. Use either only to
  inspect a fine detail. A path printed with `(unchanged)` is pixel-identical to the file there before: don't
  read it again.
- Image tokens are priced by pixels (w*h/750), not file size: JPEG doesn't help, don't convert.
- It opens its own tab and closes it, so it never navigates a working tab away. It adds a
  cache-buster so you don't see a stale bundle.

Screenshots alone don't need `share_screen` (that's for driving the screen, see mac-operator).

## The loop

1. Change the code, then **test it before it goes live**: for a mini app, `cos app preview <folder>` (cos-app-builder skill).
2. `ui-shot <url>` (default: desktop + phone, light): **2 shots per check**, in the theme you're changing. Do it
   when a change is done, not after each tweak. Take all 4 (`--themes light,dark`) once at the end of a UI change,
   or when colours or the theme change. cos UI is always checked on desktop and phone. Check **every** page, not
   just the one you changed. A **blank** screenshot means the whole app crashed. Only then install.
3. Read each image once, and never re-read one you've already seen. Check against `apps/lifeos/design/DESIGN.md` (kept for the mini apps):
   - nothing overflows the phone width (see the §13 flex `min-width: 0` gotcha)
   - headings are Cabinet Grotesk, body is Satoshi
   - no all-caps labels, no filler text
   - both themes readable (in the final 4-shot pass)
4. Fix and repeat. Only then report, and say what you looked at.

## Also useful

- A raw `agent-browser screenshot` (or a cua-driver one) is full size (~1920 px wide, ~2.3k tokens). Run
  `shot-shrink <png>` on it before you Read it (~0.4k).

- To measure instead of eyeballing:
  `agent-browser eval 'document.querySelector(".x").scrollWidth'`
  (e.g. find the element that overflows).
- Mockups and one-off HTML: serve the folder with `python3 -m http.server <port> --bind 127.0.0.1`
  and ui-shot the URL. `file://` pages can hang agent-browser.
- To show Sandesh: publish via agent-tools `publish_files` (artifacts board, behind SSO). If
  the MCP call hangs, POST to `/api/publish-files` directly (see mac-operator history, 2026-09-26).

## Don't screenshot while a routine runs

Routines drive the same Agent Chrome. ui-shot refuses while any `var/routines/*/running.json`
is live (it would capture the agent's tab and resize its window). Wait, or `--force` only if you
know nothing is running.
