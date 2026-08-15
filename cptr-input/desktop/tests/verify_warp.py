"""Stage 1a: prove the coordinate pipeline against the real screen.

Uses CGWarpMouseCursorPosition, which needs no permission, so this runs today.
Moves the cursor to computed targets, reads the position back, and restores the
original position afterwards.
"""

from __future__ import annotations

import time

import inject
import mapper
import probe_display

TARGETS = [
    ("centre",       0.5,  0.5),
    ("top-left",     0.02, 0.02),
    ("top-right",    0.98, 0.02),
    ("bottom-left",  0.02, 0.98),
    ("bottom-right", 0.98, 0.98),
    ("third across", 0.33, 0.66),
]

TOLERANCE = 2.0  # points


def main() -> int:
    d = [x for x in probe_display.displays() if x["main"]][0]
    display = mapper.Display(d["origin"][0], d["origin"][1], *d["points"])
    print(f"display : {display.width:g}x{display.height:g} pts "
          f"({d['pixels'][0]}x{d['pixels'][1]} px, {d['pixels'][0] / d['points'][0]:g}x)")
    print(f"accessibility permission : {'GRANTED' if inject.permission_status() else 'not granted'}")
    print("  (warp works without it; clicks and keys do not)\n")

    origin = inject.cursor_position()
    failures = 0
    try:
        for label, nx, ny in TARGETS:
            events = mapper.plan(display, "pointer",
                                 {"event": "move", "x": nx, "y": ny, "normalized": True})
            want_x, want_y = events[0].detail["x"], events[0].detail["y"]
            inject.warp(want_x, want_y)
            time.sleep(0.12)
            got_x, got_y = inject.cursor_position()
            dx, dy = abs(got_x - want_x), abs(got_y - want_y)
            ok = dx <= TOLERANCE and dy <= TOLERANCE
            failures += not ok
            print(f"  {label:14s} want=({want_x:7.1f},{want_y:7.1f})  "
                  f"got=({got_x:7.1f},{got_y:7.1f})  drift=({dx:.1f},{dy:.1f})  "
                  f"{'PASS' if ok else 'FAIL'}")
    finally:
        inject.warp(*origin)
        print(f"\n  cursor restored to {origin}")

    print(f"  result: {len(TARGETS) - failures}/{len(TARGETS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
