---
name: cos-card
description: Write a "cos-card" at the end of a reply so Sandesh's Chief of Staff panel shows your result as a small card (a short Markdown note, optionally a stats line and up to 3 answer buttons) instead of an AI one-liner. Use when you finish a task or need a decision from Sandesh and the outcome fits on a card: what you did or found, the numbers that matter, the choice he has to make.
---

# cos-card: your own update card

Every agent chat on aibo-mac and aibo-linux shows up as an **update card** in Sandesh's Chief of Staff
panel (`apps.personal.kingdomofluna.com/cos`). Normally a model writes a one-line summary of your last
reply. If your reply **ends with a `cos-card` block**, the panel shows your card instead: your words,
your numbers, and buttons he can tap to answer you.

Use one when you've **finished something** or **need a decision**. Skip it for small talk, progress
chatter, or when the answer is a single sentence.

## Format

A fenced block at the end of your reply: one JSON header line (all fields optional), then a short
Markdown body.

````
```cos-card
{"title": "Backups: 2 decisions", "status": "needs",
 "stats": [["8/8", "clean nights"], ["1.2 TB", "in Backblaze"], ["0", "failures"]],
 "actions": [["Keep 30 days", "Keep deleted files for 30 days"], ["Wipe too", "Wipe them with the local copy"]]}
**Keep deleted files 30 days**, or wipe them with the local copy?
**Failure alerts**: phone only, or phone + email?
The Dropbox token is revoked.
```
````

| Field | What | Limit |
|---|---|---|
| `title` | The headline: what happened or what you need | 60 chars |
| `status` | `needs` (waiting on him), `done`, `working`, `warn`, `error`, `info` | |
| `stats` | `[value, label]` pairs: the numbers that matter | 4 |
| `actions` | `[button label, the message it sends you]` | 3 buttons, 28-char labels |
| `url` | Tapping the card opens this link | |
| body | Markdown: short sentences or up to 5 bullets, **bold** for the key part | ~5 lines, then "More" |

## Rules

- **Lean.** A card is a glance, not a report. Put the detail in your reply above the card.
- **Buttons only send you a message**, as if Sandesh typed it. Make the message self-contained
  ("Keep deleted files for 30 days", not "yes"). Offer real choices only; never a button that makes
  you do something irreversible without a further check.
- **No headings, tables or images** in the body (they're flattened to plain lines).
- Valid JSON on the header, one block per reply, at the end.
- Plain words, no jargon, no internal ids or paths unless he needs them.
