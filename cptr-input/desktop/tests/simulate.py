"""Stage 0: dry-run the whole input path. Posts nothing, needs no permissions.

Feeds representative cptr client events through mapper.py and prints the CGEvents
that inject.py would post. Run it to check the coordinate maths and keycode
mapping before granting any TCC permission.
"""

from __future__ import annotations

import mapper

try:
    import probe_display
    _d = [d for d in probe_display.displays() if d["main"]][0]
    DISPLAY = mapper.Display(_d["origin"][0], _d["origin"][1], *_d["points"])
    SOURCE = f"live CGDisplayBounds ({_d['points'][0]}x{_d['points'][1]} pts)"
except Exception as exc:  # no Quartz -> still usable
    DISPLAY = mapper.Display()
    SOURCE = f"fallback default ({exc.__class__.__name__})"

CASES: list[tuple[str, str, dict]] = [
    ("move to centre",        "pointer", {"event": "move", "x": 0.5, "y": 0.5, "normalized": True}),
    ("move to top-left",      "pointer", {"event": "move", "x": 0.0, "y": 0.0, "normalized": True}),
    ("move to bottom-right",  "pointer", {"event": "move", "x": 1.0, "y": 1.0, "normalized": True}),
    ("left click down",       "pointer", {"event": "down", "x": 0.25, "y": 0.4, "normalized": True,
                                          "button": "left", "click_count": 1}),
    ("left click up",         "pointer", {"event": "up", "x": 0.25, "y": 0.4, "normalized": True,
                                          "button": "left", "click_count": 1}),
    ("double click",          "pointer", {"event": "down", "x": 0.25, "y": 0.4, "normalized": True,
                                          "button": "left", "click_count": 2}),
    ("right click",           "pointer", {"event": "down", "x": 0.6, "y": 0.6, "normalized": True,
                                          "button": "right", "click_count": 1}),
    ("out-of-range (reject)", "pointer", {"event": "move", "x": 1.4, "y": 0.5, "normalized": True}),
    ("scroll down",           "wheel",   {"x": 0.5, "y": 0.5, "normalized": True, "delta_y": 120}),
    ("type 'h'",              "key",     {"event": "keyDown", "key": "h", "code": "KeyH", "text": "h"}),
    ("type emoji",            "key",     {"event": "char", "key": "*", "code": "", "text": "\U0001f680"}),
    ("accented char",         "key",     {"event": "keyDown", "key": "e", "code": "KeyE", "text": "é"}),
    ("Cmd+C",                 "key",     {"event": "keyDown", "key": "c", "code": "KeyC",
                                          "text": "", "modifiers": mapper.CDP_META}),
    ("Cmd+Shift+4",           "key",     {"event": "keyDown", "key": "4", "code": "Digit4", "text": "",
                                          "modifiers": mapper.CDP_META | mapper.CDP_SHIFT}),
    ("Escape",                "key",     {"event": "keyDown", "key": "Escape", "code": "Escape", "text": ""}),
    ("ArrowUp",               "key",     {"event": "keyDown", "key": "ArrowUp", "code": "ArrowUp", "text": ""}),
    ("unknown key",           "key",     {"event": "keyDown", "key": "F19", "code": "F19", "text": ""}),
    ("paste",                 "paste",   {"text": "hello from the browser"}),
]


def main() -> None:
    print(f"display source : {SOURCE}")
    print(f"mapping target : origin=({DISPLAY.x:g},{DISPLAY.y:g}) "
          f"size={DISPLAY.width:g}x{DISPLAY.height:g} pts\n")

    unmapped = 0
    for label, kind, data in CASES:
        events = mapper.plan(DISPLAY, kind, data)
        if not events:
            print(f"  {label:22s} -> (rejected, no event posted)")
            continue
        for ev in events:
            if ev.kind == "UNMAPPED":
                unmapped += 1
            print(f"  {label:22s} -> {ev}")

    # drag needs button state, so show it separately
    print("\n  -- drag (button held) --")
    for ev in mapper.plan(DISPLAY, "pointer",
                          {"event": "move", "x": 0.7, "y": 0.7, "normalized": True},
                          buttons_held=1):
        print(f"  {'drag with left held':22s} -> {ev}")

    print(f"\n  unmapped keys: {unmapped}")


if __name__ == "__main__":
    main()
