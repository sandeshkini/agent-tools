#!/usr/bin/env python3
"""
agent-tools — one shared MCP server for every agent that reaches this machine (cptr's Claude
Code + OpenCode; formerly Hermes too, retired 2026-08-11).

Core job: give any agent here two ways to reach Sandesh when he isn't actively watching the
chat — **push a page** (any content type: markdown/html/svg/mermaid/code/react) for him to
review whenever he next checks his phone, or **push a bare alert** with no page at all.
Publishing a page always fires a phone notification too — he never has to be told separately
that something's ready, the tap-to-open link is already in the push.

Exposes 6 tools from this one place (add a tool here → every agent gets it, no per-adapter
work) — 2 for the artifacts board, 2 for versioned artifacts, 1 for binary/multi-file bundles,
1 for phone push:
  • publish_artifact(title, content_markdown, artifact_type="markdown", language="")
      one-shot publish (dated file, no id) → POSTs /api/publish, fires an ntfy push, returns URL.
  • create_artifact(title, content, artifact_type="markdown", language="")
      versioned publish (stable id, v1) → POSTs /api/artifacts, for content you'll revise later.
  • update_artifact(artifact_id, content, language="")
      pushes a new version (v2, v3, ...) to an existing id's SAME url — PUTs /api/artifacts/:id.
  • list_artifacts() → newest-first text listing (name/type/version/id/source/url), so an agent
      can find an id before calling update_artifact.
  • publish_files(title, files) → publish one or more local files (any type, binary included) as
      a single artifact — a whole webpage export, an app bundle, or just one arbitrary file (PDF,
      image, zip, ...). Each file is read straight off local disk by THIS server, never retyped
      by the calling agent as text — see the tool's own docstring for why that distinction matters
      and where local_path must resolve on each deployment (Docker vs host install differ).
  • notify(title, message, priority) → sends a phone push via ntfy, no page — for a bare alert
      that doesn't need a page (e.g. "build finished, 3 tests failing"), separate from the
      auto-push the publish tools above already give you for free.

Every artifact this server publishes/creates is auto-tagged with a single `source` field — which
system + which machine, e.g. "cptr@aibo-linux" — composed from SOURCE_LABEL + COMPUTER_LABEL env
vars (no default on the latter; must be set per-deployment, see docker-compose.yml / install.sh)
so the board can show/filter "who sent this" without the calling agent ever declaring it. Not an
agent-facing tool parameter on purpose — it's a deployment fact, not a per-call judgment call.

Served over Streamable HTTP at /mcp so every agent reaches it by URL:
  - containers (Claude Code / OpenCode via cptr) → http://mcp-tools:8000/mcp   (compose network)
  - a second machine's host install (install.sh) → http://127.0.0.1:8009/mcp   (its own instance)
MCP tool calls surface in each agent as ordinary tool_use/tool_result → they render as the
adapters' native tool-call cards.
"""
import base64
import datetime
import json
import os
import re
import urllib.request

from mcp.server.fastmcp import FastMCP

ARTIFACTS_API = os.getenv("ARTIFACTS_API", "http://artifacts:8080/api/publish")
_ARTIFACTS_BASE = ARTIFACTS_API.rsplit("/api/", 1)[0]
ARTIFACTS_CREATE_API = f"{_ARTIFACTS_BASE}/api/artifacts"
ARTIFACTS_LIST_API = f"{_ARTIFACTS_BASE}/api/list"
ARTIFACTS_FILES_API = f"{_ARTIFACTS_BASE}/api/publish-files"
PUBLISH_TOKEN = os.getenv("PUBLISH_TOKEN", "")
# Per-file / per-bundle caps for publish_files — generous (these are real binaries now, not
# hand-typed text) but bounded so one runaway call can't hang the request or the server.
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
# Stamped onto every artifact this instance publishes/creates, so the board can show/filter "who
# sent this" without trusting the calling LLM to type it correctly per-call — a deployment fact,
# not something the agent decides. ONE field on the wire (`source`), composed from two env vars
# so it's easy to set per-machine without hardcoding a combined string everywhere:
#   SOURCE_LABEL   which system (default "cptr")
#   COMPUTER_LABEL which physical machine (e.g. "aibo-linux", "aibo-mac" — see machines.md for
#                  canonical names). A container's own hostname is useless here (just its
#                  container ID), so this MUST be set explicitly per machine in that machine's
#                  own agent-tools/.env — no reliable auto-detect from inside Docker.
# Result looks like "cptr@aibo-linux"; falls back to just SOURCE_LABEL if COMPUTER_LABEL was
# never set anywhere for this deployment.
SOURCE_LABEL = os.getenv("SOURCE_LABEL", "cptr")
COMPUTER_LABEL = os.getenv("COMPUTER_LABEL", "unknown")
ORIGIN_LABEL = f"{SOURCE_LABEL}@{COMPUTER_LABEL}" if COMPUTER_LABEL != "unknown" else SOURCE_LABEL
PUBLIC_BASE = os.getenv("PUBLIC_BASE", "http://localhost:8080")
NTFY_BASE = os.getenv("NTFY_URL", "http://ntfy").rstrip("/")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "aibo")
NTFY_TOKEN = os.getenv("NTFY_TOKEN", "")

# 0.0.0.0 is correct (and required) inside Docker, where only the compose port mapping
# (127.0.0.1:8009:8000, loopback-only) actually exposes it. Running this directly on a host
# (no Docker in front) needs the override — HOST=127.0.0.1 — or an unauthenticated MCP
# endpoint (no per-caller auth; publish_artifact/notify hold the real tokens server-side) ends
# up reachable from the whole LAN instead of just this machine.
mcp = FastMCP("agent-tools", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))


@mcp.tool()
def publish_artifact(title: str, content_markdown: str, artifact_type: str = "markdown", language: str = "") -> str:
    """Push ANY kind of content — markdown notes, an HTML mockup, an SVG, a Mermaid diagram, a
    code snippet, or a React component — to a durable, shareable page on the artifacts board
    ($PUBLIC_BASE) for Sandesh to review later. This is how you hand him something to look at
    when he isn't watching right now: publishing automatically fires a phone push via ntfy (tap
    → opens straight to the page), so you never need to separately tell him it's ready — that
    happens on its own the moment this call succeeds. Returns the page URL.

    WHEN TO USE: only when the user EXPLICITLY asks you to publish / share / save a page, OR for
    autonomous / scheduled work where no human is watching to do it themselves (e.g. a scheduled daily
    brief, or a long task finishing while he's away and you want him to review the result). For an
    ordinary reply do NOT auto-publish — the user has a built-in "Publish" button on every
    message and can pin any reply themselves. Prefer answering in chat; publish only when a durable,
    shareable link genuinely adds value, OR when he's not around to see the answer any other way.

    :param title: Short title for the page.
    :param content_markdown: The page body — despite the param name, this holds whatever
        `artifact_type` calls for (markdown text, an SVG document, Mermaid diagram source, a code
        snippet, or a React component source). Kept named `content_markdown` so existing callers
        that only pass (title, content_markdown) keep working exactly as before — the default
        `artifact_type` is still "markdown".
    :param artifact_type: one of:
        - "markdown" (default) — rendered with the board's markdown styling
        - "html"    — served as-authored, open it directly
        - "svg"     — inlined on the page, with a "view source" toggle
        - "mermaid" — diagram source, rendered client-side
        - "code"    — syntax-highlighted; pass the language name via `language` (e.g. "python")
        - "react"   — a React component. Define it as `function App() { ... }` (or
          `export default function App() { ... }` — either works) using only JSX + the `App` name;
          it's written as a plain, self-running index.html — open the URL and it runs, nothing else
          to do. No import needed for React itself (it's already in scope); a few extra libraries
          (recharts, lucide-react, d3) are available to `import` if the component needs charts/icons.
    :param language: for artifact_type="code" only — the language name (e.g. "python", "bash", "sql").
    """
    try:
        body = json.dumps({"title": title, "type": artifact_type, "language": language,
                           "content": content_markdown, "source": ORIGIN_LABEL}).encode()
        req = urllib.request.Request(ARTIFACTS_API, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-Publish-Token": PUBLISH_TOKEN})
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read().decode() or "{}")
        return "Published: " + PUBLIC_BASE + resp.get("url", "")
    except Exception as e:
        return f"publish_artifact failed: {e}"


@mcp.tool()
def create_artifact(title: str, content: str, artifact_type: str = "markdown", language: str = "") -> str:
    """Same idea as publish_artifact — any content type, for Sandesh to review, fires a phone
    push automatically — but with a stable id you can push new versions to later instead of
    littering the board with near-duplicate dated pages. Use this one instead of publish_artifact
    when you expect to revise this specific piece of content (call update_artifact with the
    returned id to push v2/v3/... to the SAME url, same phone-push behavior on every version).

    :param title: Short title for the page.
    :param content: markdown text / SVG document / Mermaid diagram source / code snippet / React
        component source, depending on `artifact_type` — see publish_artifact's docstring for
        exactly what each type means and (for "react") the `App`-naming rule.
    :param artifact_type: markdown (default) | html | svg | mermaid | code | react.
    :param language: for artifact_type="code" only — the language name (e.g. "python").
    """
    try:
        body = json.dumps({"title": title, "type": artifact_type, "language": language,
                           "content": content, "source": ORIGIN_LABEL}).encode()
        req = urllib.request.Request(ARTIFACTS_CREATE_API, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-Publish-Token": PUBLISH_TOKEN})
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read().decode() or "{}")
        return f"Created (id={resp.get('id')}, v1): " + PUBLIC_BASE + resp.get("url", "")
    except Exception as e:
        return f"create_artifact failed: {e}"


@mcp.tool()
def update_artifact(artifact_id: str, content: str, language: str = "") -> str:
    """Push a new version of an existing artifact (one created via create_artifact) to its
    SAME url — use this when refining something you already published, instead of creating a
    new artifact or using publish_artifact, so the board gets a navigable version history at one
    stable link rather than a pile of near-duplicates.

    :param artifact_id: the id returned by create_artifact (or found via list_artifacts).
    :param content: the new version's content — same shape as create_artifact's `content` param,
        for the artifact's existing type (type can't change on update).
    :param language: for code-type artifacts only; omit to keep the artifact's current language.
    """
    try:
        body = json.dumps({"content": content, "language": language}).encode()
        req = urllib.request.Request(f"{ARTIFACTS_CREATE_API}/{artifact_id}", data=body, method="PUT",
                                     headers={"Content-Type": "application/json",
                                              "X-Publish-Token": PUBLISH_TOKEN})
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read().decode() or "{}")
        return f"Updated (v{resp.get('version')}): " + PUBLIC_BASE + resp.get("url", "")
    except Exception as e:
        return f"update_artifact failed: {e}"


@mcp.tool()
def list_artifacts() -> str:
    """List published artifacts (newest first, name/type/version/id/url) — use this to find an
    artifact's id before calling update_artifact, or to check what's already on the board."""
    try:
        req = urllib.request.Request(ARTIFACTS_LIST_API, headers={"X-Publish-Token": PUBLISH_TOKEN})
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read().decode() or "{}")
        items = resp.get("items", [])
        if not items:
            return "No artifacts published yet."
        lines = []
        for i in items[:30]:
            tag = f" id={i['id']}" if i.get("id") else ""
            ver = f" (v{i['versions']})" if i.get("versions", 1) > 1 else ""
            src = f" src={i['source']}" if i.get("source") else ""
            lines.append(f"- [{i.get('type')}] {i.get('name')}{ver}{tag}{src} -> {PUBLIC_BASE}{i.get('href')}")
        return "\n".join(lines)
    except Exception as e:
        return f"list_artifacts failed: {e}"


@mcp.tool()
def publish_files(title: str, files: list) -> str:
    """Publish one or more local files as a single artifact — a whole webpage (HTML + CSS/JS/
    images, relative links preserved), a self-contained app bundle, or just one arbitrary file
    (PDF, image, zip, anything). Use this instead of publish_artifact/create_artifact whenever
    the content is binary, already sitting on disk, or too large to safely retype as text: each
    file is read directly off local disk BY THIS SERVER and uploaded as raw bytes — never passed
    through you as generated text — so nothing gets silently corrupted or truncated no matter how
    large or opaque (a giant base64 blob typed out by an LLM mid-conversation is exactly the
    failure mode this avoids — it happened once, dropped a character, corrupted an image).

    WHERE THE FILES MUST LIVE — differs per deployment:
      - On aibo-linux this tool runs in Docker and can only see /tmp (read-only). Stage files
        there first if they live elsewhere, e.g. `cp report.pdf /tmp/`.
      - On a host install (aibo-mac / aibo-dev / sage-agent — no Docker) this runs as a normal
        process with full local filesystem access — any path this machine can read works as-is.

    :param title: Short title for the artifact.
    :param files: list of {"local_path": "/abs/path/on/this/machine", "dest_path": "optional
        relative/name.ext"}. dest_path defaults to the file's own basename. Give one file
        dest_path "index.html" to make a multi-file bundle open directly as a webpage at its
        base URL; a lone file (no index.html, only one entry) is served directly at its own URL.
        With multiple files and no index.html, a plain listing page is generated automatically
        so the bundle is still directly openable instead of a dead end.
    """
    if not files:
        return "publish_files failed: no files given"
    try:
        payload_files = []
        total = 0
        for f in files:
            local_path = f.get("local_path") or f.get("localPath")
            if not local_path:
                return "publish_files failed: every file needs local_path"
            dest_path = f.get("dest_path") or f.get("destPath") or os.path.basename(local_path)
            if ".." in dest_path.replace("\\", "/").split("/"):
                return f"publish_files failed: dest_path '{dest_path}' may not contain '..'"
            try:
                size = os.path.getsize(local_path)
            except OSError as e:
                return f"publish_files failed: can't read {local_path} ({e}) — remember: on the Docker deployment this process can only see /tmp"
            if size > MAX_FILE_BYTES:
                return f"publish_files failed: {local_path} is {size} bytes, over the {MAX_FILE_BYTES}-byte per-file limit"
            total += size
            if total > MAX_TOTAL_BYTES:
                return f"publish_files failed: bundle exceeds the {MAX_TOTAL_BYTES}-byte total limit"
            with open(local_path, "rb") as fh:
                content_b64 = base64.b64encode(fh.read()).decode("ascii")
            payload_files.append({"path": dest_path, "content_base64": content_b64})
        body = json.dumps({"title": title, "source": ORIGIN_LABEL, "files": payload_files}).encode()
        req = urllib.request.Request(ARTIFACTS_FILES_API, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-Publish-Token": PUBLISH_TOKEN})
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read().decode() or "{}")
        return "Published: " + PUBLIC_BASE + resp.get("url", "")
    except Exception as e:
        return f"publish_files failed: {e}"


@mcp.tool()
def notify(title: str, message: str, priority: str = "default") -> str:
    """Send a push notification to the user's phone (ntfy) — a quick custom heads-up / alert, no page.

    WHEN TO USE: for a genuinely out-of-band alert the user should see while away — e.g. "build done, 3
    tests failing", or the result of a scheduled/autonomous task. You do NOT need this just to say a turn
    finished or that you need input: the hub ALREADY auto-pushes "done" and "needs input" for every turn.
    Use only for notify-worthy custom messages the automatic pushes don't cover.

    :param title: Short notification title.
    :param message: The notification body.
    :param priority: one of min, low, default, high, urgent.
    """
    try:
        payload = {"topic": NTFY_TOPIC, "title": title, "message": message}
        pr = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}.get(priority)
        if pr:
            payload["priority"] = pr
        headers = {"Content-Type": "application/json"}
        if NTFY_TOKEN:
            headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
        req = urllib.request.Request(NTFY_BASE + "/", data=json.dumps(payload).encode(),
                                     method="POST", headers=headers)
        urllib.request.urlopen(req, timeout=10).read()
        return f"Notification sent: {title}"
    except Exception as e:
        return f"notify failed: {e}"


if __name__ == "__main__":
    print(f"[mcp-tools] agent-tools MCP on :{os.getenv('PORT','8000')}/mcp "
          f"(publish→{ARTIFACTS_API}, ntfy→{NTFY_BASE}/{NTFY_TOPIC})", flush=True)
    mcp.run(transport="streamable-http")
