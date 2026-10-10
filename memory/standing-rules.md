# Sandesh's standing rules (every agent, both machines)

A short, checked list of rules that hold until he changes them. It's loaded into every agent's instructions
(registry.toml `[instructions.standing-rules]`). The `memory` MCP has the details and the history; this list is the part
that must never be missed by a search. Change a line only when Sandesh says so directly, and tell him in your reply.

**Working with him**
- Do machine chores yourself (licence prompts, dialogs, desktop clicks); don't ask him for them. (2026-10-10)
- Keep updates, cards and recaps lean. Prototypes show only the core idea, calm and simple. (2026-10-06, 2026-10-10)
- Agent lists show only agents that are working or need his decision. (2026-10-06)
- cos is used on desktop and phone: design and check both, and say what changed on each. (2026-10-09)
- Accepted risks, don't re-flag: agents run as root on aibo-linux; tokens in the private ai-memory transcripts; apps a
  little behind on updates. Declined, don't re-propose: keeping deleted files in B2, a backup-failure alert, a second
  backup disk, backing up Nextcloud's config. (2026-10-06, 2026-10-08)

**Machines and setup**
- aibo-linux runs agent infrastructure (memory, ntfy, artifacts, mcp-tools, agent messaging); aibo-mac runs his
  personal apps (cos, the Chief, LifeOS). (2026-10-08)
- Both machines keep the same folders under ~/Documents/personal. Every MCP server and skill comes from agent-tools
  `registry.toml`; never add them by hand. (2026-10-07)
- Plans on hold go in aibo-server/research/parked/<plan>/. (2026-10-07)
- Agent chats start with full approval. Default model Claude Opus 5.5, reasoning effort medium unless he says
  otherwise. (2026-10-06, 2026-10-07)

**Secrets, logins, money**
- Each machine has its own ~/Documents/secrets/keys.env (the source of truth, never shared between machines), backed up
  to 1Password as "keys.env - <machine>". Secrets never go into agent configs, memory or docs. (2026-10-06/07/08)
- Agents sign in to websites only through 1Password. (2026-10-07)
- Use low-cost models; core flows and routines run on his Claude plan, not OpenRouter. Each system has its own
  OpenRouter key. (2026-10-07)
- Splitwise: never add property tax; only expenses dated from 2026-10-01; one-off expenses are his; don't touch the
  "Amara - Car" entries; "Joint CC" uses the statement balance. (2026-10-08)

**Family**
- Nextcloud and other family-facing apps: family keep logging in exactly as today, no 2FA for them. Any change to DNS,
  Pangolin/SSO or how family reach an app needs an exact description of what they'd see and his OK first. (2026-10-08)

**Memory**
- Record only his rules, preferences, decisions (with the reason) and non-obvious setup; not progress or "pending". The
  agent that heard it from him records it; passed-on decisions aren't recorded again. See the `memory` skill.
