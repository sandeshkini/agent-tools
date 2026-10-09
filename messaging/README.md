# messaging: agent messaging for the Chief of Staff

The cos agent-messaging service (code: `sandeshkini/cos`, `hub/messaging_service.py`,
`Dockerfile.messaging`), here because aibo-linux runs **agent infrastructure** (what agents call, across
machines) and aibo-mac runs **personal apps** (Sandesh, 2026-10-08). It holds the message log, the rules
(star: agents talk to the Chief; hop, rate and open-ask limits) and the agents' `crew` tools.

- `127.0.0.1:8793` on aibo-linux; aibo-mac reaches it through `fleet-tunnel` (`127.0.0.1:8793`), the cos
  hub (a container on aibo-mac) through `host.docker.internal:8793` (VM firewall allow-list).
- Nodes (cos-node ≥ 0.4, `--crew-url http://127.0.0.1:8793`) send agents' crew calls here; the hub pushes
  the roster (agents, names, states), records the Chief's sends, and takes the outbox (deliveries into
  chats, names agents propose) and the Chief's agent mail.
- Token: `COS_MESSAGING_TOKEN` (keys.env on both machines; `.env` here; nodes:
  `~/.config/cos-node/messaging-token`).
- Data: `messaging/data/messaging.db` (SQLite, gitignored). Config: `messaging/config.json`.
- Build: `docker compose up -d --build messaging` (context `../apps/cos`: `git -C ../apps/cos pull` first).
- Check: `curl -s 127.0.0.1:8793/health`.
