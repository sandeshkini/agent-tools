# ntfy — the notification bus

Push notifications: **an agent finished**, **an agent needs your input**, **an artifact was
published**. Delivered to the ntfy app on your phone.

One bus, runs on aibo only — every machine (aibo-mac, aibo-dev, sage-agent) pushes to this same
instance rather than running its own, so all notifications land in one place regardless of which
machine did the work. (There's no actual hub/node profile split in the compose file — just one
shared service every machine's `mcp-tools`/agent points at, same pattern as the artifacts board.)

## Why ntfy and not browser push

Notifications need to land even when no browser tab is open (cptr's chat isn't watched 24/7),
so a dedicated push service is the right tool regardless of what any given web frontend supports.

`NTFY_UPSTREAM_BASE_URL=https://ntfy.sh` matters on iOS: self-hosted ntfy can't wake an iPhone on
its own, so it relays through ntfy.sh for instant delivery. Only the topic name transits the relay
when the message is delivered this way — keep the topic unguessable.

## ⚠️ `data/lib/user.db` IS your access control

`NTFY_AUTH_DEFAULT_ACCESS=deny-all` means **nothing** is readable or writable without credentials
from that SQLite file. Consequences:

- **Never commit it.** It's gitignored; this repo is public. It holds your notification credentials.
- **Never lose it.** Restore it before starting ntfy anywhere else, or every client 403s.
- It's a single portable SQLite DB, so it moves cleanly — just make sure it actually moves.

Back it up: `cp ntfy/data/lib/user.db <somewhere-safe>/user.db.$(date +%F)`

Manage users/ACLs through the container:
```bash
docker exec -it ntfy ntfy user list
docker exec -it ntfy ntfy access <user> <topic> rw
```

## Config

| Env | Default | Notes |
|---|---|---|
| `NTFY_PUBLIC_URL` | `https://ntfy.kingdomofluna.com` | what the server advertises to clients |
| `NTFY_DOMAIN` | `ntfy.kingdomofluna.com` | Pangolin resource hostname (`localhost:8095` target — direct port, no Traefik, removed 2026-08-15) |
| `NTFY_PORT` | `8095` | host + LAN access |
| `NTFY_DATA` | `./ntfy/data` | `lib/` (user.db) + `cache/` |
| `NTFY_URL` | `http://ntfy` | **internal** URL other containers push to (no public round-trip) |
| `NTFY_TOPIC` / `NTFY_TOKEN` | — | what `mcp-tools`' `notify()` and every publish auto-push use |

## Publishing from a service

Use the **JSON body** API, not the header API — HTTP headers are Latin-1 only, so a title with an
emoji or em-dash throws `Cannot convert argument to a ByteString` and the push is *silently dropped*:

```jsonc
POST http://ntfy/          // Authorization: Bearer $NTFY_TOKEN
{ "topic": "…", "title": "…", "message": "…", "click": "https://…", "tags": ["white_check_mark"] }
```
