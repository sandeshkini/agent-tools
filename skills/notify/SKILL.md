---
name: notify
description: Send a push notification to Sandesh's phone through ntfy. Use for an out-of-band alert he should see while away, such as the result of a scheduled or autonomous task, a long job finishing, or something that needs his attention.
---

# notify

A short phone push, with no page attached.

## When

- A scheduled or autonomous task has a result.
- A long job finished, or failed, while he's away.
- Something needs his attention and he may not be watching the chat.

## When not to

- To say a turn finished or that you need input: cptr already pushes both.
- After publishing to the artifacts board: publishing already pushes the link.
- For routine progress. Every push interrupts him, so send only what he'd want to be
  interrupted for.

## How

Title: what happened, in a few words. Message: the one thing he needs to know or do.
Priority: `default` for most; `high` if it needs action today; `urgent` only for
something broken right now; `low` for FYI.

If you have the agent-tools MCP tools, call `notify(title, message, priority)`.
Otherwise:

```
agent-tools notify "Nightly import failed" "3 of 40 shops errored — Shopify 429s. Retrying at 06:00." --priority high
```
