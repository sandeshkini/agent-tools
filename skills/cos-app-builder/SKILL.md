---
name: cos-app-builder
description: "Build a mini app that the Chief of Staff hub hosts (cos docs/apps.md): a folder with app.json, a UI and, if it keeps data, server/app.py. Use when Sandesh asks for a new app, tracker or page in cos, or when moving a LifeOS part into cos. Covers picking static vs worker, cos app new / preview / validate / install / rollback, the cos_apps SDK, tools and grants, and checking it with ui-shot before saying it's done."
---

# Building a cos mini app

A mini app is a small app the cos hub hosts itself: no container, proxy or MCP server of its own. The full
reference is `apps/cos/docs/apps.md`. Commands run from the cos checkout (`~/Documents/personal/apps/cos`) with
its venv: `.venv/bin/python scripts/cos app …`. Work in your own folder or worktree, never in the cos checkout.

## 1. Pick the runtime

- **static**: one `ui/index.html` (inline scripts and styles, ≤ 512 KB). It runs in the panel's locked box (no
  network, can't reach the hub). Its data goes in `cos.store` (collections of JSON items the hub keeps). Use it
  for small things, and anything Sandesh didn't ask to be trusted.
- **worker**: a UI plus `server/app.py`, run in the hub's app worker. It owns a folder of plain files under the
  instance's `apps.data_root` (`ctx.files`), and can offer tools to the Chief. Use it when the data should be
  files in the docs repo, or when the Chief needs to read or change it.

## 2. Make, preview, look

```
.venv/bin/python scripts/cos app new <id> --runtime worker|static --dir ~/somewhere   # a working skeleton
.venv/bin/python scripts/cos app preview ~/somewhere/<id> --port <free port>          # as the panel shows it
ui-shot "http://127.0.0.1:<port>/?page=<key>" --sizes desktop,phone --themes light,dark
.venv/bin/python scripts/cos app validate ~/somewhere/<id>
```

Read every screenshot (the `ui-check` skill). The preview uses a throwaway data folder; `--data DIR` points it at
real files, but only with a copy, never the docs repo itself. Pick a port nothing else uses (4398 and 4399 are
often taken).

- **Look:** use the panel's tokens. In a worker app: `<link rel="stylesheet" href="/kit/tokens.css">`, then
  `var(--surface)`, `--card`, `--ink`, `--line`, `--accent`, `--font`, `--font-head`, `--r-md`. A static app gets
  `--cos-bg`, `--cos-card`, `--cos-ink`, `--cos-line`, `--cos-accent`, `--cos-font`, `--cos-radius`.
- **Phones:** the shell's title bar names the app, so hide your own title at ≤ 768px.
- **Frame protocol:** a worker app does `import { cosFrame } from '/kit/cos-frame.js'`, then calls
  `frame.ready(page, title)` once it has drawn, and follows `navigate` to its pages.
- **Static apps:** read `cos.page`, and listen for the `cos-page` and `cos-theme` events.

## 3. The server module (worker apps)

```python
from cos_apps import route, tool, HTTPError

@route("GET", "/items")                     # answered at /apps/<id>/api/items
def items(ctx, req):                        # req.params, req.query, req.body (JSON)
    return json.loads(ctx.read_text("items.json", "[]"))

@tool(level="read", doc="The open items")   # read | draft | act (act: the agent asks Sandesh first)
def open_items(ctx) -> str: ...
```

- **Files:** stay inside `ctx.files`, through `ctx.path` / `ctx.read_text` / `ctx.write_text` (atomic). Hold
  `ctx.lock` around read-modify-write.
- **Other apps:** read them with `ctx.call("money", "GET /overview")`, never their files.
- **Keep it to one file:** `server/app.py` is loaded alone, so sibling imports don't work.
- **Badges and live cards:** a page `badge` (`{route, where, tone}`) and `components` (`route` instead of
  `source`) read your own routes. See docs/apps.md and pack-spec.md §8.

## 4. Install, and only when asked

Installing changes what Sandesh sees in cos, so do it only when he (or your lead) said so, and only on a hub
where `scripts/cos app list` doesn't say apps are off.

```
.venv/bin/python scripts/cos app install <folder>      # every version is kept; raise "version" each time
.venv/bin/python scripts/cos app rollback <id>         # back to the version before
```

Then check it in the real panel with ui-shot (`http://127.0.0.1:4300/?space=<id>:<page>`) on desktop and phone,
in light and dark.

Tools reach an agent only through grants in hub.json, which you don't edit yourself; ask your lead:
- the Chief: `grants.chief`, e.g. `app:<id>:read`
- a lead: its group's `lead.grants`, which must name the app (`app:<id>:read`), and never act

## Don'ts

- No secrets in app.json, the UI or the module. Data stays as files where it is.
- Never run a preview on 4200/4300 (live hubs), or against the real docs folder.
- No personal names or data in apps that ship with cos itself (`examples/`); personal apps live in their own repo.
