"""Memory self-test: does a later fact supersede an earlier one?

Writes two contradicting episodes into a scratch group (`selftest`), checks that the old fact is
invalidated and the new ones are current, then clears the group. ~2 minutes, a few cents.

  uv run --with "mcp>=1.12,<2" python memory/selftest.py [http://127.0.0.1:8012/mcp/]
"""
import asyncio, json, sys, time
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8012/mcp/"
G = "selftest"
def txt(r):
    return "\n".join(getattr(c, "text", "") for c in r.content)
async def main():
    async with streamablehttp_client(URL) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print("tools:", ", ".join(tools))
            await s.call_tool("clear_graph", {"group_ids": [G]})
            eps = [
              ("secrets-old", "On 2026-09-20 Sandesh decided that all secrets live in the macOS Keychain and that keys.env is deprecated and will be deleted."),
              ("secrets-new", "On 2026-10-06 Sandesh decided that keys.env is the source of truth for all secrets. The macOS Keychain only keeps a copy. This replaces the earlier Keychain decision."),
            ]
            for name, body in eps:
                t0 = time.time()
                r = await s.call_tool("add_memory", {"name": name, "episode_body": body, "group_id": G, "source": "text", "source_description": "memory selftest"})
                print("add", name, "→", txt(r)[:100])
                # add_memory is queued: wait until the episode shows up
                for _ in range(90):
                    got = txt(await s.call_tool("get_episodes", {"group_ids": [G], "max_episodes": 10}))
                    if name in got: break
                    await asyncio.sleep(2)
                print(f"  processed in {time.time()-t0:.0f}s")
                await asyncio.sleep(3)
            r = await s.call_tool("search_memory_facts", {"query": "where do Sandesh's secrets live, keys.env or Keychain?", "group_ids": [G], "max_facts": 10})
            facts = json.loads(txt(r)).get("facts", [])
            old = [f for f in facts if "2026-09-20" in f["fact"] or "deprecated" in f["fact"]]
            new = [f for f in facts if "source of truth" in f["fact"] and not f.get("invalid_at")]
            ok1 = bool(old) and all(f.get("invalid_at") or f.get("expired_at") for f in old)
            ok2 = bool(new)
            ok3 = all(f.get("episodes") for f in facts)
            for ok, what in [(ok1, "old Keychain fact is superseded (kept, marked invalid)"), (ok2, "new keys.env fact is current"), (ok3, "every fact links to its source episode")]:
                print(("✓ " if ok else "✗ ") + what)
            await s.call_tool("clear_graph", {"group_ids": [G]})
            print("selftest group cleared")
            sys.exit(0 if ok1 and ok2 and ok3 else 1)
asyncio.run(main())
