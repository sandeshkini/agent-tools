"""IPv4 loopback shim for cptr.

cptr binds IPv6-only ([::1]:38217) because newt/Pangolin reach it that way. But
cptr's own chrome-mode viewer hardcodes http://127.0.0.1:38217 for the encoder
page, and IPv4 127.0.0.1 is refused -> chrome mode is broken ("site can't be
reached").

This forwards 127.0.0.1:PORT (IPv4) -> [::1]:PORT (IPv6), so the viewer's IPv4
URL reaches cptr. Raw TCP passthrough, so HTTP and WebSocket both work. No cptr
code is touched, so a `uv tool upgrade cptr` cannot break it.

Usage: python shim.py <port> [<port> ...]   (default 38217)
"""

import asyncio
import sys


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


def make_handler(port: int):
    async def handle(client_r, client_w):
        try:
            up_r, up_w = await asyncio.open_connection("::1", port)
        except OSError:
            client_w.close()
            return
        await asyncio.gather(
            pipe(client_r, up_w),
            pipe(up_r, client_w),
        )
    return handle


async def main(ports: list[int]) -> None:
    servers = []
    for port in ports:
        srv = await asyncio.start_server(make_handler(port), host="127.0.0.1", port=port)
        servers.append(srv)
        print(f"shim: 127.0.0.1:{port} -> [::1]:{port}", flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    ports = [int(a) for a in sys.argv[1:]] or [38217]
    asyncio.run(main(ports))
