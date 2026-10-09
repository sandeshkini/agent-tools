---
name: cos-card
description: Write a "cos-card" at the end of a reply so Sandesh's Chief of Staff panel shows your result as a small card (a short Markdown note, optionally a link, up to 3 answer buttons and a row of real numbers) instead of an AI one-liner. Use when you finish a task or need a decision from Sandesh and the outcome fits on a card: what you did or found, the numbers that matter, the choice he has to make.
---

# cos-card: your own update card

Every agent chat on aibo-mac and aibo-linux shows up as an **update card** in Sandesh's Chief of Staff
panel (`cos.personal.kingdomofluna.com`). Normally a model writes a one-line summary of your last
reply. If your reply **ends with a `cos-card` block**, the panel shows your card instead: your words,
your numbers, and buttons he can tap to answer you.

Use one when you've **finished something** or **need a decision**. Skip it for small talk, progress
chatter, or when the answer is a single sentence.

## Format

A fenced block at the end of your reply: one JSON header line (all fields optional), then a short
Markdown body. **Most cards are just a title and a short Markdown note.** Pick the shape that fits
what you're saying:

A plain note (what you did, with the link he needs):

````
```cos-card
{"title": "Test page updated", "status": "done", "url": "https://artifacts.kingdomofluna.com/a/example"}
Five new example cards, each a different shape. **Tap the card to open the page.**
```
````

A checklist:

````
```cos-card
{"title": "Mac cleanup", "status": "done"}
- [x] Cleared 14 GB of old Xcode simulators
- [x] Emptied the Downloads folder older than 90 days
- [ ] Photos library: left alone, needs your call
```
````

Key: value lines:

````
```cos-card
{"title": "Flight booked", "status": "done"}
**When:** Fri 10 Oct, 7:40 → 10:05
**Seat:** 14A, window
**Cost:** $312, on the Amex
```
````

A question with buttons:

````
```cos-card
{"title": "Backups: keep deleted files?", "status": "needs",
 "actions": [["Keep 30 days", "Keep deleted files in Backblaze for 30 days"], ["Wipe them", "Wipe deleted files along with the local copy"]]}
> Keep deleted files for 30 days, or wipe them with the local copy?

30 days costs about $1 a month more.
```
````

Stats, **only when there are real numbers that matter** (counts, sizes, money, durations). Never
use stats for words ("Full / width" is not a number); if you don't have 2 or more real numbers,
leave `stats` out:

````
```cos-card
{"title": "Backups: 8 clean nights", "status": "done",
 "stats": [["8/8", "clean nights"], ["1.2 TB", "in Backblaze"], ["0", "failures"]]}
Nothing to do. The Dropbox token is revoked as planned.
```
````

| Field | What | Limit |
|---|---|---|
| `title` | The headline: what happened or what you need | 60 chars |
| `status` | `needs` (waiting on him), `done`, `working`, `warn`, `error`, `info` | |
| `url` | **The link he needs** (a page, a PR, a report). The card gets an "Open page" button | |
| `actions` | `[button label, the message it sends you]` | 3 buttons, 28-char labels |
| `stats` | Optional `[value, label]` pairs: real numbers only | 4 |
| body | Markdown: a sentence or two, a list, a checklist, `**Key:** value` lines, a `>` quote | ~5 lines, then "More" |

## Rules

- **Lean.** A card is a glance, not a report. Put the detail in your reply above the card.
- **Links go in the card**: in `url`, or written out in the body. Sandesh often sees only the card,
  not your reply, so never write "the link above" or "see my reply".
- **Stats are optional.** Most cards have none. Use them only for real numbers that matter.
- **Buttons only send you a message**, as if Sandesh typed it. Make the message self-contained
  ("Keep deleted files for 30 days", not "yes"). Offer real choices only; never a button that makes
  you do something irreversible without a further check.
- **No headings, tables or images** in the body (they're flattened to plain lines).
- Valid JSON on the header, one block per reply, at the end.
- **Write the card as reply text.** Planning it in your thinking doesn't show it. Do your tool calls
  first, then write the card in your final text, and never point to a card you haven't written.
- The opening and closing fences go **on their own lines**. Don't write the fence name inline in
  prose (e.g. when talking about cards): the panel would read it as an empty card.
- Plain words, no jargon, no internal ids or paths unless he needs them.

## cos-card or cos-ui?

A `cos-card` is one fixed shape: a note, a link, up to 3 buttons, a stats row. When the result has more
structure than that (several items each with a state, a table, numbers he'd want side by side, a choice
he'd tweak with a slider or form, or live data such as agents or today's habits), write a ```cos-ui
block instead (the `cos-ui` skill). Whichever block comes last in your reply becomes your update card.
