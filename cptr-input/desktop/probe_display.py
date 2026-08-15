"""Stage 0 probe: read real display geometry. Needs no TCC permission.

Confirms the point-vs-pixel assumption behind mapper.Display.
"""

from __future__ import annotations

import Quartz


def displays() -> list[dict]:
    err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
    if err:
        raise RuntimeError(f"CGGetActiveDisplayList failed: {err}")
    out = []
    main = Quartz.CGMainDisplayID()
    for did in ids[:count]:
        bounds = Quartz.CGDisplayBounds(did)
        mode = Quartz.CGDisplayCopyDisplayMode(did)
        out.append({
            "id": int(did),
            "main": did == main,
            # bounds are in POINTS -- this is CGEvent's coordinate space
            "points": (round(bounds.size.width), round(bounds.size.height)),
            "origin": (round(bounds.origin.x), round(bounds.origin.y)),
            # pixels are what the video capture produces
            "pixels": (Quartz.CGDisplayModeGetPixelWidth(mode),
                       Quartz.CGDisplayModeGetPixelHeight(mode)),
        })
    return out


if __name__ == "__main__":
    for d in displays():
        sx = d["pixels"][0] / d["points"][0] if d["points"][0] else 0
        print(f"  display {d['id']}{' (main)' if d['main'] else ''}")
        print(f"    points  : {d['points'][0]} x {d['points'][1]}   <- CGEvent space")
        print(f"    pixels  : {d['pixels'][0]} x {d['pixels'][1]}   <- capture space")
        print(f"    origin  : {d['origin']}")
        print(f"    scale   : {sx:g}x")
