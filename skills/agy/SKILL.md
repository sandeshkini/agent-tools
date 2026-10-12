---
name: agy
description: "Antigravity CLI (agy; Gemini 3.x by default, also Claude and GPT-OSS models) as a first-class agent with the same trust and abilities as Claude Code: give it real work (commands, edits, commits, fixes) and it reports back to you. Run it as a crew member chat (model agent:agy/<id>) for longer tasks, or one-shot via agy-ask. Use when Sandesh asks for agy/Antigravity/Gemini, to parallelise work, or for a different model family."
---

# agy (Antigravity CLI) from an agent

Installed 2026-09-29: `~/.local/bin/agy` (v1.2.13, updates itself; `agy update`). Signed in
as Sandesh's Google account (token in the macOS keychain). Settings and history:
`~/.gemini/antigravity-cli/`.

## agy is a full agent (Sandesh, 2026-10-11)

"agy can be trusted ... it should be a first class citizen in terms of agent ability. You run it, it reports to you, but it can do
everything you do." Same trust and abilities as Claude Code: shell, edits, commits, deploys. Give it real work, not only reviews, and
don't describe it as untrusted. Quota is Google's, not the Claude plan.

**Longer or multi-step work: start it as a crew member** (it gets the crew tools and reports back to you):
`crew start_member` with `model: agent:agy/gemini-3.1-pro-high` (or `agent:agy/gemini-3.8-flash-medium` for quick jobs). The friendly
name "agy" doesn't resolve; use the exact `agent:agy/<id>` (ids from `agy models`). Needs cptr fork patch 22 (live on aibo-linux and
aibo-mac) and an `agy` entry in that cptr's `agents.profiles`.

**One-shot tasks: `agy-ask`** runs it in your folder (or `--cwd`) with full permissions:

```
agy-ask "Check the hub at 192.168.0.111 and report its open ports"
agy-ask "Fix the failing test, commit, and tell me what changed" --cwd ~/Documents/personal/apps/lifeos
agy-ask "Review this diff for bugs" --file /tmp/change.diff --model gemini-3.1-pro-high
agy-ask "…" --json        # full result: conversation_id, usage
```

`--file` paths are named in the prompt; `--dir` adds folders to its workspace; default timeout 600 s. `--readonly` exists for the rare
case you want its opinion on something without it acting on it.

**Long tasks via agy-ask:** a big job can end with an empty `response` (its final message cut off). Resume the same conversation
and ask it to print its result: `agy -p "Print your result now" --conversation <id> --output-format json --dangerously-skip-permissions`.
For anything long, prefer a crew member chat.

## Models

Default: **Gemini 3.8 Flash** (fast, ~2–6 s for short answers). For harder reasoning:
`--model gemini-3.1-pro-high`. Also offered: `claude-opus-4-6-thinking`, `claude-sonnet-4-6`,
`gpt-oss-120b-medium`. For a real second opinion pick a different family from the one you are.

## Data

Same rules as every agent (see the standing rules): secrets stay in keys.env and never go into configs, memory or docs.
