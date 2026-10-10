---
name: ui-check
description: >-
  Look at the UI you just built or changed, the way Sandesh will see it, before saying it's done. Use whenever you change a web app's frontend (LifeOS, dashboards, mockups, artifacts): screenshot it at desktop and phone sizes, in light and dark, read the images, and judge them against the app's design system. Uses ui-shot (agent-browser in the Agent Chrome).
---

# UI check: look before you say "done"

A build that compiles isn't a UI that works. Every visible change gets looked at.

## The command

```
ui-shot <url | lifeos:<tab>> [--sizes desktop,phone,tablet,wide] [--themes light,dark] [--click "<tab text>"]
```

- `lifeos:<tab>` opens LifeOS on this Mac (tabs: habits, workout, home, projects, year,
  financials, notes). It goes through a **read-only** local proxy (GET only), because the real URL
  is behind Pangolin SSO. Screenshots can't change data.
- `--click "Markets"` clicks a visible button or tab after load, for sub-tabs.
- It prints PNG paths (`/tmp/ui-shot/<name>-<size>-<theme>.png`). **Open them with Read and
  actually look.**
- It opens its own tab and closes it, so it never navigates a working tab away. It adds a
  cache-buster so you don't see a stale bundle.

## The loop

1. Change the code, then **test the build before it goes live.** Build it as a separate
   container, e.g. LifeOS:
   `docker build -t lifeos:test . && docker run -d --name lifeos-test -p 127.0.0.1:4002:4000 -v ~/Documents/personal/docs:/docs:ro lifeos:test`
   (docs mounted **read-only**; add `PA_API_URL/TOKEN` env for System).
2. `LIFEOS_UPSTREAM=http://127.0.0.1:4002 ui-shot lifeos:<tab> --sizes desktop,phone --themes light,dark`
   for **every** tab, not just the one you changed. A **blank** screenshot means the
   whole app crashed (2026-09-26: a stray `,,` in the TABS array blanked every page; a
   successful build and a 200 from the API both hid it).
   Only then deploy: `docker tag lifeos-app lifeos-rollback:<label> && docker compose up -d --build`,
   and check live that `#root` isn't empty on each tab. Roll back with
   `docker tag lifeos-rollback:<label> lifeos-app && docker compose up -d --no-build`.
3. Read every image. Check against `apps/lifeos/design/DESIGN.md`:
   - nothing overflows the phone width (see the §13 flex `min-width: 0` gotcha)
   - headings are Cabinet Grotesk, body is Satoshi
   - no all-caps labels, no filler text
   - both themes readable
4. Fix and repeat. Only then report, and say what you looked at.

## Also useful

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
