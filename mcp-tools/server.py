#!/usr/bin/env python3
"""
agent-tools — one shared MCP server for ALL agents (claude / hermes / opencode).

Exposes two tools that every agent gets from this single place (add a tool here → every agent
gets it, no per-adapter work):
  • publish_artifact(title, content_markdown) → publishes a page to the artifacts board
    (POSTs the artifacts API, which also fires an ntfy phone push) and returns the URL.
  • notify(title, message, priority)          → sends a phone push via ntfy (no page).

Served over Streamable HTTP at /mcp so every agent reaches it by URL:
  - containers (owui-claude, opencode) → http://mcp-tools:8000/mcp   (compose network)
  - host Hermes                        → http://127.0.0.1:<published>/mcp
MCP tool calls surface in each agent as ordinary tool_use/tool_result → they render as the
adapters' native OWUI tool cards ("✓ View Result from …").
"""
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
PUBLISH_TOKEN = os.getenv("PUBLISH_TOKEN", "")
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
    """Publish content as a shareable page on the artifacts board ($PUBLIC_BASE/artifacts)
    and send a phone notification. Returns the page URL.

    WHEN TO USE: only when the user EXPLICITLY asks you to publish / share / save a page, OR for
    autonomous / scheduled work where no human is watching to do it themselves (e.g. a scheduled daily
    brief). For an ordinary reply do NOT auto-publish — the user has a built-in "Publish" button on every
    message and can pin any reply themselves. Prefer answering in chat; publish only when a durable,
    shareable link genuinely adds value.

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
                           "content": content_markdown}).encode()
        req = urllib.request.Request(ARTIFACTS_API, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-Publish-Token": PUBLISH_TOKEN})
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read().decode() or "{}")
        return "Published: " + PUBLIC_BASE + resp.get("url", "")
    except Exception as e:
        return f"publish_artifact failed: {e}"


@mcp.tool()
def create_artifact(title: str, content: str, artifact_type: str = "markdown", language: str = "") -> str:
    """Create a new artifact with a stable id, on the artifacts board. Use this instead of
    publish_artifact when you expect to revise this specific piece of content later — call
    update_artifact with the returned id to push a new version to the SAME url, instead of
    publish_artifact leaving a trail of near-duplicate dated pages on the board.

    :param title: Short title for the page.
    :param content: markdown text / SVG document / Mermaid diagram source / code snippet / React
        component source, depending on `artifact_type` — see publish_artifact's docstring for
        exactly what each type means and (for "react") the `App`-naming rule.
    :param artifact_type: markdown (default) | html | svg | mermaid | code | react.
    :param language: for artifact_type="code" only — the language name (e.g. "python").
    """
    try:
        body = json.dumps({"title": title, "type": artifact_type, "language": language,
                           "content": content}).encode()
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
        resp = json.loads(urllib.request.urlopen(ARTIFACTS_LIST_API, timeout=15).read().decode() or "{}")
        items = resp.get("items", [])
        if not items:
            return "No artifacts published yet."
        lines = []
        for i in items[:30]:
            tag = f" id={i['id']}" if i.get("id") else ""
            ver = f" (v{i['versions']})" if i.get("versions", 1) > 1 else ""
            lines.append(f"- [{i.get('type')}] {i.get('name')}{ver}{tag} -> {PUBLIC_BASE}{i.get('href')}")
        return "\n".join(lines)
    except Exception as e:
        return f"list_artifacts failed: {e}"


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
