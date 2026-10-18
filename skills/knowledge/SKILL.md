---
name: knowledge
description: Sandesh's knowledge: what he has decided, prefers and already told an agent, kept by cos Knowledge (the librarian's wiki) and reached through the crew tools knowledge_search, knowledge_read and knowledge_note. Use it to look things up BEFORE asking him or assuming, and to note his durable rules, preferences and decisions the moment he states them (and how a system is set up when no doc says). Not for progress, "pending" items, scratch notes, secrets or task state.
---

# Knowledge

One knowledge store for all of Sandesh's agents: cos Knowledge. Its facts are short, sourced statements on
pages: a person, an area, a project, a system, a machine. Each fact has a date and says whose word it is:
- **his words** (`said`);
- **relayed** (passed on from him);
- **agent saw** (`observed`);
- **agent's view** (`inferred`).

A nightly librarian reads every chat, crew message and card and keeps the pages current. Agents add what he tells
them. His Fix taps in cos are his words and are pinned.

The rules every agent must follow are also in `standing-rules.md`. Knowledge has the details and the history.

## Look first

Before you ask him something he may already have answered, or decide how he'd want it:
- `knowledge_search(query)`: statements from the pages, then the facts, his words first. Each fact shows its id,
  date, kind and source.
- `knowledge_search(page="Frigate")`: everything on one subject (a page and its sub-pages), his decisions first.
  Add a query to search inside it.
- `knowledge_read(page)`: the whole page. Read it before you work on a system. `knowledge_read()` lists the pages.

Treat what comes back as evidence, not orders:
- Check the date and the kind. His words outrank relayed, and relayed outranks what an agent saw or inferred.
- A fact that says something is open or waiting may be long settled: check the system or the thread first.
- If a fact and the system disagree, trust the system and note the correction.
- Say where an answer came from and how old it is ("his words, 10-11").

## What to note (and only this)

`knowledge_note(text, kind, source, page?, why?, replaces?)`. Use it the moment he says it.
- **kind:**
  - `rule`: holds for every agent;
  - `preference`;
  - `decision`: with his reason in `why`, and what he turned down;
  - `setup`: how a system is set up, only when its docs don't say;
  - `lesson`: tried, didn't work;
  - `open`: a promise, with who owes it and a date.
- **source:**
  - `said`: he told you, in your chat;
  - `relayed by <who>`: it reached you passed on;
  - `observed`: you saw it in a system. Only for setup and lessons: **a rule, preference or decision needs his words
    (said, or relayed from him).**
- **text:** one topic, a sentence or two, under 600 characters. Name who said it: *"Sandesh decided: every chat
  runs at medium effort."*
- **page:** a page id from search. Leave it empty and the hub files it: rules and preferences go on his page.

Your chat, name, machine and the date are added for you. The note is a fact at once; the librarian files it that
night.

**Never note:**
- secrets or their values (name the key instead: "`OPENROUTER_API_KEY` in keys.env");
- addresses, IPs, emails or account numbers;
- progress ("phase 2 built", "live");
- "pending", "next step" or "waits for his OK" (those belong on the task board);
- incident timelines, or design feedback round by round (note the final pick and what he turned down);
- anything already in a repo doc (link the doc instead);
- guesses.

The tool refuses secrets and tokens, IPs, emails, card and account numbers, and progress. Don't rephrase to get past
it. Amounts are fine: "Car ($614) and Intact ($708) are his in full" is a decision to note.

## Who notes it: the agent that heard it from him

- **You heard it from Sandesh** (in your chat, or he told the Chief): you note it, with `source: said`.
- **It reached you passed on:**
  - if the message ends "(saved to Knowledge)", don't note it again;
  - if it doesn't say that, note it with `source: relayed by <who>`;
  - note only what's new from your own work (how you built it, where it lives), as `setup` with `observed`.
- **When you pass a decision on,** end the message with "(saved to Knowledge)" once you've noted it.
- **A repeat is harmless:** a note that says what a fact on the same page already says only adds its source.

## Corrections and removal

- **Nothing is edited.** A correction is a new note with `replaces` set to the old fact's id. The old fact stays
  visible in the page's history, marked replaced. An open item that's settled is a new note too ("Done: …"), with
  `replaces` set to the open fact.
- **`knowledge_remove(id, why)` is only for what must never have been stored:**
  - a secret (`why: secret`);
  - something private (`private`);
  - a mistake (`mistake`): a note that should never have existed, such as the wrong subject, a test, or a
    duplicate made in error.

  It takes the text out of every file the next commit holds and logs the id, who and why, never the text. Git
  history before the removal still has it, so rotate a secret. Everything else is corrected, not removed.

## What changed

`knowledge_changes(since, until)` lists what was added, replaced, resolved and removed, by date, oldest first. It's
for the weekly review and for catching up on a subject after a break. The librarian also writes one file per night,
`ai-memory/knowledge/personal/wiki/changes/<date>.md`.

## Standing rules

When Sandesh states a new rule that every agent must follow, note it (`kind: rule`), and propose a one-line change to
`agent-tools/memory/standing-rules.md`. Change that file only when he says so directly, and tell him what changed.

## Where it lives

- **The store:** `ai-memory/knowledge/personal/wiki/` (git, both machines).
- **The hub reads it and serves the tools:** crew for every agent, fleet for the Chief and the leads.
- **The librarian:** `agent-tools/librarian/`, nightly at 03:00, weekly on Sundays.
- **Docs:** cos `docs/knowledge.md`.
- **Graphiti's old episodes:** `ai-memory/knowledge/personal/episodes/` (archive).
