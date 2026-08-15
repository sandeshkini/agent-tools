# artifacts — the publish board

Durable pages published by agents (and by you): **markdown/html/svg/mermaid/code/react**
(rendered through the board's own chrome, or opened raw for html/react) and **apps**
(a folder with an `index.html` — interactive mini-apps, opened raw).

Unlike Open WebUI's built-in artifacts pane — which is a *live preview* of HTML/CSS/JS in the
current chat, with no backend, no database and **no URL** — this gives an artifact a permanent
home you can link to and open from your phone.

Two flavors of storage, both served from the same board:
- **One-shot** (`/api/publish`) — a dated file, no identity, republishing the same title same-day
  overwrites. Original, still fully supported, forever.
- **Versioned** (`/api/artifacts`) — a stable id + `v1/, v2/, ...` history at one URL, for content
  you expect to revise. Added later, additive — doesn't touch or replace one-shot.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/api/publish` | `x-publish-token` header | one-shot write: new dated file, fire a push, return its URL |
| `POST` | `/api/artifacts` | `x-publish-token` header | versioned create: new id at v1, fire a push |
| `PUT` | `/api/artifacts/:id` | `x-publish-token` header | versioned update: push vN to an existing id, same URL |
| `GET` | `/api/artifacts/:id` | none¹ | metadata + version list for one artifact |
| `GET` | `/api/list` | none¹ | JSON feed of everything (one-shot + versioned), newest first |
| `GET` | `/` | none¹ | the board — everything, newest first, filterable |
| `GET` | `/summaries/<file>` | none¹ | `.md` rendered with `marked`; svg/mermaid/code get their own renderer; anything else raw |
| `GET` | `/apps/<slug>/…` | none¹ | static serve; bare dir → `index.html` |
| `GET` | `/a/:id`, `/a/:id/v/:n` | none¹ | versioned artifact viewer — latest or a specific version |

¹ *No auth in the app itself* — reads are protected by whatever fronts it (here: Pangolin SSO on
`artifacts.kingdomofluna.com`; the separate `push.artifacts.kingdomofluna.com` hostname has SSO
**off** and is restricted server-side to only the token-gated write routes above — see
`~/Documents/aibo-server/Services/artifacts.md`). Don't expose this directly to the internet
without a front door.

**Publish/create body** (both endpoints share this shape):
```
{
  title,                      required
  content,                    required
  type,                       markdown (default) | html | svg | mermaid | code | react | app
  language,                   type:"code" only — e.g. "python", "bash"
  source,                     optional — which SYSTEM published this (e.g. "cptr", "aibo-mac-pipeline")
  computer                    optional — which MACHINE sent it (e.g. "aibo-linux", "aibo-mac")
}
```
`kind: "summary"|"app"` + `format: "md"|"html"` (the pre-type-system shape) still work on
`/api/publish` for back-compat — `type` is just the preferred field now, covering 5 more kinds.

`source`/`computer` are free text, shown on the board and filterable there once more than one
distinct value exists (a single-value filter is hidden rather than shown with nothing to filter).
Omit either and it shows as `manual`/`unknown` — that's also what all pre-existing content (from
before these fields existed) backfills to. `update_artifact`/`PUT` accepts the same two fields to
*change* an existing artifact's source/computer; omit to keep whatever it was set to at creation.

**Update body** (`PUT /api/artifacts/:id`): `{ content, language?, source?, computer? }` — type
can't change on update.

→ one-shot: `{ ok: true, url: "/summaries/2026-08-10-my-title.md" }`
→ versioned: `{ ok: true, id: "my-title-a1b2c3", url: "/a/my-title-a1b2c3", version: 1 }`

Filenames are `YYYY-MM-DD-<slugified-title>`, so publishing the same title twice on the same day
**overwrites** (one-shot only — versioned artifacts never overwrite, they add a version).
Publishing with `type: html`/`react` serves the document raw — no markdown wrapper, no width cap,
no preview chrome — open the URL and it runs.

## Config

| Env | Default | Notes |
|---|---|---|
| `PUBLISH_TOKEN` | — | required; POST/PUT is rejected 401 without a match |
| `CONTENT_DIR` | `/content` | bind-mounted from `${ARTIFACTS_CONTENT:-./artifacts/content}` |
| `BASE_PATH` | *(empty)* | external prefix; served at domain root now, not under `/artifacts` |
| `PUBLIC_BASE` | `https://artifacts.kingdomofluna.com` | used to build click-through links in pushes |
| `PUSH_ONLY_HOST` | `push.artifacts.kingdomofluna.com` | requests on this Host header may ONLY reach the 3 write routes — everything else 404s |
| `NTFY_URL` / `NTFY_TOPIC` / `NTFY_TOKEN` | — | publishing auto-pushes; **no-op if unset** |

`mcp-tools` (the MCP server agents actually call through) has its own env for the two provenance
fields — `SOURCE_LABEL` (default `cptr`) and `COMPUTER_LABEL` (no safe default; a Docker
container's own hostname is meaningless, so this must be set per-machine in that machine's
`agent-tools/.env` — see `machines.md` for canonical names) — stamped onto every artifact it
publishes/creates automatically, so the calling agent never has to declare them itself.

## Data

`./artifacts/content/` is **gitignored** — this repo is public and published pages are personal
(they can contain host paths, system detail, internal notes). The code is tracked; the content is not.
Back it up yourself; it is the only copy.

## Multiple machines, one board

There's no hub/node split — every machine running this stack serves its own independent board.
What actually happens today: multiple machines' agents (and the aibo-mac pipeline, hitting
`push.artifacts.*` directly) all publish to the **same** board on aibo, tagging each publish with
`source`/`computer` so the board stays legible about where everything came from. If a second
machine ever needs its *own* board instead, that's a from-scratch feature, not something toggled
on here.
