"""Command-line client for the agent-tools MCP server.

Any agent with a shell gets the same six tools as one wired to the MCP server
(Codex, scripts, cron). The server holds the publish/ntfy tokens and does the
work; this only forwards the call.
"""
import argparse
import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = os.environ.get("AGENT_TOOLS_MCP", "http://127.0.0.1:8009/mcp")
TYPES = ["markdown", "html", "svg", "mermaid", "code", "react"]


async def call(tool, args):
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
    text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
    if result.isError:
        sys.exit(f"✗ {tool}: {text}")
    return text


def content_of(ns):
    if ns.file:
        with open(ns.file, encoding="utf-8") as f:
            return f.read()
    if sys.stdin.isatty():
        sys.exit("give the content with --file <path> or on stdin")
    return sys.stdin.read()


def file_specs(items):
    specs = []
    for item in items:
        local, _, dest = item.partition("=")
        spec = {"local_path": os.path.abspath(os.path.expanduser(local))}
        if not os.path.isfile(spec["local_path"]):
            sys.exit(f"no such file: {local}")
        if dest:
            spec["dest_path"] = dest
        specs.append(spec)
    return specs


def main():
    p = argparse.ArgumentParser(prog="agent-tools", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("notify", help="phone push, no page")
    n.add_argument("title")
    n.add_argument("message")
    n.add_argument("--priority", choices=["min", "low", "default", "high", "urgent"], default="default")

    f = sub.add_parser("publish-files", help="publish local files (any type) as one artifact")
    f.add_argument("title")
    f.add_argument("files", nargs="+", help="path, or path=dest; dest index.html makes it a webpage")

    for name, help_ in [("publish", "one-shot page"), ("create", "versioned page (prints its id)")]:
        c = sub.add_parser(name, help=help_)
        c.add_argument("title")
        c.add_argument("--type", choices=TYPES, default="markdown")
        c.add_argument("--language", default="", help="for --type code")
        c.add_argument("--file", help="read content from a file instead of stdin")

    u = sub.add_parser("update", help="new version of a created artifact, same URL")
    u.add_argument("id")
    u.add_argument("--language", default="")
    u.add_argument("--file")

    sub.add_parser("list", help="artifacts, newest first")

    ns = p.parse_args()
    if ns.cmd == "notify":
        out = call("notify", {"title": ns.title, "message": ns.message, "priority": ns.priority})
    elif ns.cmd == "publish-files":
        out = call("publish_files", {"title": ns.title, "files": file_specs(ns.files)})
    elif ns.cmd == "publish":
        out = call("publish_artifact", {"title": ns.title, "content_markdown": content_of(ns),
                                        "artifact_type": ns.type, "language": ns.language})
    elif ns.cmd == "create":
        out = call("create_artifact", {"title": ns.title, "content": content_of(ns),
                                       "artifact_type": ns.type, "language": ns.language})
    elif ns.cmd == "update":
        args = {"artifact_id": ns.id, "content": content_of(ns)}
        if ns.language:
            args["language"] = ns.language
        out = call("update_artifact", args)
    else:
        out = call("list_artifacts", {})
    print(asyncio.run(out) if asyncio.iscoroutine(out) else out)


if __name__ == "__main__":
    main()
