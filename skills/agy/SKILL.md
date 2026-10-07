---
name: agy
description: Ask Google's Antigravity CLI (agy; Gemini 3.x by default, also Claude and GPT-OSS models) a one-shot question from another agent — a second opinion from a different model family, a review of a plan or diff, or reading/summarising files you hand it. Use when Sandesh asks for "agy", "Antigravity", "Gemini" or a second opinion, or when a cross-check by another model would help. Always call it through agy-ask, never bare agy from a real folder.
---

# agy (Antigravity CLI) from an agent

Installed 2026-09-29: `~/.local/bin/agy` (v1.2.13, updates itself; `agy update`). Signed in
as Sandesh's Google account (token in the macOS keychain). Settings and history:
`~/.gemini/antigravity-cli/`.

## Always use the wrapper

```
agy-ask "Summarise the risks in this plan" --file /tmp/plan.md
agy-ask "Which of these two is clearer and why?" --file a.md --file b.md
agy-ask "Explain what this backend does" --dir ~/Documents/personal/apps/lifeos/backend
agy-ask "Review this diff for bugs" --file /tmp/change.diff --model gemini-3.1-pro-high
agy-ask "…" --json        # full result: conversation_id, usage, denied_actions
```

It starts agy in an **empty scratch folder** holding only copies of the `--file`s, adds
`--dir` folders read-only, prints the answer and deletes the folder. Exit 1 + a reason on
stderr if agy was blocked, timed out (`--timeout`, default 300 s) or answered nothing. It
refuses paths that look like secrets.

**Why not bare `agy -p`:** at sign-in, `/Users/sandesh` (the whole home folder) was trusted
as a workspace, and headless agy reads any file under the folder it starts in without asking.
Started from `~` or `~/Documents` it could read `Documents/secrets/keys.env`.

## What headless agy can and can't do (tested 2026-09-29)

| Can | Can't (auto-denied, no prompt) |
|---|---|
| Answer from its own knowledge | Run shell commands |
| Read files in its folder and in `--dir` folders | Open URLs / search the web |
| Pick a model: `--model` (list: `agy models`) | Write or edit files |
| JSON output with token usage | Read outside its folder (`..`, other paths) |
| | Read piped stdin (it tries a command and is denied) — pass a `--file` instead |

agy reports `"status": "SUCCESS"` even when blocked, with an empty `response` and a
`denied_actions` list; agy-ask turns that into an error. Don't work around the blocks with
`--dangerously-skip-permissions` or by adding allow-rules to its settings.json: if the task
needs commands, web or edits, do it yourself and give agy the results as a file.

## Models

Default: **Gemini 3.8 Flash** (fast, ~2–6 s for short answers). For harder reasoning:
`--model gemini-3.1-pro-high`. Also offered: `claude-opus-4-6-thinking`, `claude-sonnet-4-6`,
`gpt-oss-120b-medium`. For a real second opinion pick a different family from the one you are.

## What not to send

Whatever you pass goes to Google. No secrets, tokens or keys. No emails, bank or card
statements, or other personal financial documents unless Sandesh asked for that exact thing.
