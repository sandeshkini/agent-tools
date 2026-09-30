---
name: artifacts
description: Publish a page, report, diagram, screenshot or any files to Sandesh's artifacts board (artifacts.kingdomofluna.com), which pushes the link to his phone. Use when asked to publish, share, or save something as a page or file, or when autonomous or scheduled work finishes with a result nobody is watching for.
---

# artifacts

The artifacts board hosts anything as a durable page with a shareable URL. Every
publish also sends a phone push with a tap-to-open link, so never send a separate
notification for it.

## When

- The user asks you to publish, share, or save something as a page or file.
- Autonomous or scheduled work finishes and nobody is watching the chat.

Not for ordinary replies: answer in chat. The user can publish any message themselves.

## Which call

| You have | Use |
|---|---|
| Files on disk: an HTML report with its images, PNGs, a PDF, a zip | **publish files** |
| Text you're writing now, published once | **publish** |
| Text you'll revise, at one stable URL | **create**, then **update** by id |
| An id you need | **list** |

Never retype a file's contents to publish them; binary or large content gets
corrupted on the way. Publish the file. To publish a webpage, give its HTML file the
destination `index.html` and include its images and assets alongside, using the
relative paths the page uses.

Content types for publish/create: `markdown` (default), `html`, `svg`, `mermaid`,
`code` (with a language), `react` (a component named `App`).

## How

If you have the agent-tools MCP tools, use them: `publish_files`, `publish_artifact`,
`create_artifact`, `update_artifact`, `list_artifacts`.

Otherwise use the `agent-tools` command. It calls the same server, which holds the
tokens:

```
agent-tools publish-files "Review: my-branch" out/index.html out/a.png out/b.png
agent-tools publish-files "Weekly numbers" ~/out/summary.pdf
agent-tools publish "Notes on X" --file notes.md
agent-tools create "Plan" --type markdown --file plan.md      # prints the id and URL
agent-tools update <id> --file plan.md
agent-tools list
```

`publish-files` takes `path` or `path=dest`. The destination defaults to the file's
own name. A file whose destination is `index.html` makes the bundle a webpage, and the
page's other files need the relative paths it links to (`out/img/a.png=img/a.png`).

Give the user the URL it returns.
