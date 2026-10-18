---
name: fanout
description: "Hand a list of small, independent tasks to cheap parallel workers (free DeepSeek on OpenCode Zen by default) with `fanout`, which then runs a verify command (build, tests, lint) and makes the workers fix their own failures. Use when Sandesh wants to save Claude tokens on bulk, well-scoped work: many separate files or screens, tests, conversions, boilerplate, doc pages. You write only the job file; the workers do the work. Not for design decisions, tasks that edit the same file, or anything without a machine check."
---

# fanout: you plan, cheap workers do

Sandesh wants to save Claude tokens (2026-10-17): the orchestrator defines tasks, the workers write and fix. Free DeepSeek
(`opencode/deepseek-v4.1-flash-free`, OpenCode Zen) costs nothing; its output was good on 16 SwiftUI screens (see
aibo-server/research/local-ai-worker/).

```
fanout job.json --dry-run     # check the plan
fanout job.json               # run: worktree -> workers write -> verify -> workers fix -> commit -> report
fanout --example              # a job file to copy
```

## Writing a job (JSON, details in `fanout --help`)

- **tasks**: one per output, each with a `prompt` and the exact `files` it may write. Tasks must not share files.
- **edit**: `true` on a task that changes files that already exist (a fix): without it, a task runs only when one of its files is missing.
- **rules**: conventions every worker must follow (names, imports, "touch only your files"). Give the worker a sample file to copy
  in the rules or the prompt ("follow cos/Screens/InboxScreen.swift") instead of describing a style.
- **verify**: the one command that proves it works (`xcodebuild …`, `pytest -q`, `ruff check`). With an `error_regex`
  (group 1 file, 2 line, 3 message) errors go back to the worker that owns the file; without it, one worker gets the output.
- **after**: commands once verify passes (screenshots, a smoke run).
- Work happens in a worktree on branch `fanout/<name>` next to the repo, so main is untouched. The report is
  `~/.fanout/runs/<name>-<time>/report.md`.

## What it needs from you

1. Split the work into independent pieces with a machine check. If it can't be checked by a command, don't fan it out.
2. Afterwards read the report, then look at the result (screenshots, diff). Cheap workers get layout and edge cases wrong
   (one Board screen was clipped). Send a fix task or fix it yourself.
3. Merge or delete the branch. `fanout` never merges.

## Limits seen so far

- The free tier drops requests silently when flooded (7 of 10 workers wrote nothing at 10 at once, 3 of 3 and 6 of 6 were fine).
  Default parallel is 6; `fanout` retries missing files up to 3 times at lower parallelism.
- Worker stalls of minutes were seen on 2026-10-16, none on 10-17. A worker has a 300 s timeout.
- Free-tier data handling is unknown: don't send private code or personal data through it. Use `model` to switch to a paid one
  (`openrouter/deepseek/deepseek-v4.1-flash`, about 3 cents a task) when that matters.
- OpenCode takes its folder from `$PWD`, not the process cwd, and refuses writes in a git worktree unless `external_directory`
  is allowed. `fanout` handles both; if you wrap `opencode run` yourself, do the same.
