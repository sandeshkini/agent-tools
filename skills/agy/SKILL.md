---
name: agy
description: "Hand Google's Antigravity CLI (agy; Gemini 3.x by default, also Claude and GPT-OSS models) a one-shot task from another agent — full trust by default (it can run commands, edit and browse), or --readonly for a second opinion from a different model family, a review of a plan or diff, or summarising files. Use when Sandesh asks for \"agy\", \"Antigravity\", \"Gemini\" or a second opinion, or when a cross-check by another model would help. Call it through agy-ask: full trust is the default; add --readonly for one-shot second opinions on files you don't want touched."
---

# agy (Antigravity CLI) from an agent

Installed 2026-09-29: `~/.local/bin/agy` (v1.2.13, updates itself; `agy update`). Signed in
as Sandesh's Google account (token in the macOS keychain). Settings and history:
`~/.gemini/antigravity-cli/`.

## Two ways agy runs (2026-10-11)

- **As a builder agent** (a cptr chat on the `agy` profile, chosen by Sandesh or your lead): trusted like Claude
  Code, full approval, a normal checkout or worktree, with the crew tools (`crew report` etc.). Sandesh's
  decision: "trust it full", no separate user, no clean HOME, no sandbox. Quota is Google's, not the Claude plan.
  The adapter is cptr fork patch 22 (branch `aibo-agy-adapter`, f78746a; not on a live node until deployed).
- **As a one-shot helper from another agent** (everything below): through `agy-ask`, **full trust by default**
  (Sandesh, 2026-10-11: "give it full trust"): it runs in your current folder (or `--cwd`) with
  `--dangerously-skip-permissions`, so it can run shell commands, edit files, browse and read anything, like a trusted
  Claude Code agent. `--readonly` gives the old safe mode when you only want an opinion.

## Use the wrapper (for one-shot tasks)

```
agy-ask "Check the hub at 192.168.0.111 and report its open ports"        # full trust, in the current folder
agy-ask "Fix the failing test and tell me what you changed" --cwd ~/Documents/personal/apps/lifeos
agy-ask "Review this diff for bugs" --file /tmp/change.diff --model gemini-3.1-pro-high
agy-ask "Which of these two is clearer?" --file a.md --file b.md --readonly   # opinion only, scratch folder
agy-ask "…" --json        # full result: conversation_id, usage, denied_actions
```

Full trust: `--file` paths are named in the prompt (agy reads them where they are); `--dir` adds folders to its
workspace; default timeout 600 s. `--readonly`: an empty scratch folder with COPIES of the `--file`s, `--dir` added
read-only, no shell/web/edits, and paths that look like secrets are refused. Exit 1 + a reason on stderr if agy was
blocked, timed out or answered nothing.

**Pick the folder deliberately:** in full-trust mode agy works in (and can change) the folder you start it in. Start it
where the task is, not in `~`.

## What headless agy can do

| Full trust (default) | `--readonly` |
|---|---|
| Shell commands, file edits, web, any file | Answer from its own knowledge; read its folder and `--dir` folders only |
| Verified 2026-10-11: ran port checks and `ip neigh` on aibo-linux and reported correctly | Shell/web/edits/outside reads auto-denied (reports SUCCESS with empty answer; agy-ask turns that into an error) |

Both: pick a model with `--model` (list: `agy models`), JSON output with token usage. Neither reads piped stdin; pass a
`--file` instead.

## Models

Default: **Gemini 3.8 Flash** (fast, ~2–6 s for short answers). For harder reasoning:
`--model gemini-3.1-pro-high`. Also offered: `claude-opus-4-6-thinking`, `claude-sonnet-4-6`,
`gpt-oss-120b-medium`. For a real second opinion pick a different family from the one you are.

## What not to send

Whatever you pass goes to Google. No secrets, tokens or keys. No emails, bank or card
statements, or other personal financial documents unless Sandesh asked for that exact thing.
