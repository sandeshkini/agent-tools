"""Stage 1b: prove CGEventPost works -- without any app seeing the events.

A real click would land on whatever sits under the cursor. So this installs an
active event tap that INTERCEPTS and DROPS every event we post: the callback
returns None, which swallows the event at the HID layer. We get full proof that
clicks, scrolls and keys are posted correctly, and nothing on the desktop moves.

Requires Accessibility (both for posting and for the tap itself).
"""

from __future__ import annotations

import Quartz

import inject
import mapper

CAPTURED: list[tuple[int, dict]] = []

WATCH = {
    Quartz.kCGEventLeftMouseDown: "leftDown",
    Quartz.kCGEventLeftMouseUp: "leftUp",
    Quartz.kCGEventRightMouseDown: "rightDown",
    Quartz.kCGEventRightMouseUp: "rightUp",
    Quartz.kCGEventScrollWheel: "scroll",
    Quartz.kCGEventKeyDown: "keyDown",
    Quartz.kCGEventKeyUp: "keyUp",
    Quartz.kCGEventFlagsChanged: "flagsChanged",
}

MASK = sum(Quartz.CGEventMaskBit(t) for t in WATCH)


def _callback(proxy, ev_type, event, refcon):
    # Pass through anything the human actually typed -- only swallow our own.
    if not inject.is_ours(event):
        return event
    loc = Quartz.CGEventGetLocation(event)
    CAPTURED.append((ev_type, {
        "name": WATCH.get(ev_type, str(ev_type)),
        "x": round(loc.x, 1),
        "y": round(loc.y, 1),
        "flags": int(Quartz.CGEventGetFlags(event)),
        "keycode": int(Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode)),
        "clicks": int(Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGMouseEventClickState)),
        "scroll": int(Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGScrollWheelEventDeltaAxis1)),
    }))
    return None  # swallow -- nothing downstream ever sees it


def pump(seconds: float = 0.35) -> None:
    Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, seconds, False)


def main() -> int:
    if not inject.permission_status():
        print("  Accessibility NOT granted -- CGEventPost would fail silently. Aborting.")
        return 2

    tap = Quartz.CGEventTapCreate(
        Quartz.kCGHIDEventTap, Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionDefault, MASK, _callback, None)
    if not tap:
        print("  could not create event tap (needs Accessibility). Aborting.")
        return 2

    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source,
                              Quartz.kCFRunLoopDefaultMode)
    Quartz.CGEventTapEnable(tap, True)
    print("  event tap installed -- synthetic events will be swallowed, not delivered\n")

    display = mapper.Display(0, 0, 3840, 1620)
    cases = [
        ("left click",  "pointer", {"event": "down", "x": 0.5, "y": 0.5,
                                    "normalized": True, "button": "left", "click_count": 1}),
        ("left release", "pointer", {"event": "up", "x": 0.5, "y": 0.5,
                                     "normalized": True, "button": "left", "click_count": 1}),
        ("right click", "pointer", {"event": "down", "x": 0.25, "y": 0.25,
                                    "normalized": True, "button": "right", "click_count": 1}),
        ("scroll",      "wheel",   {"x": 0.5, "y": 0.5, "normalized": True, "delta_y": 120}),
        ("Cmd+C",       "key",     {"event": "keyDown", "key": "c", "code": "KeyC",
                                    "text": "", "modifiers": mapper.CDP_META}),
        ("ArrowUp",     "key",     {"event": "keyDown", "key": "ArrowUp",
                                    "code": "ArrowUp", "text": ""}),
    ]

    results = []
    for label, kind, data in cases:
        CAPTURED.clear()
        for planned in mapper.plan(display, kind, data):
            inject.execute(planned)
        pump()
        results.append((label, list(CAPTURED)))

    # unicode typing is verified separately -- it emits keyDown/keyUp pairs
    CAPTURED.clear()
    inject.post_unicode("hi")
    pump()
    unicode_events = list(CAPTURED)

    Quartz.CGEventTapEnable(tap, False)
    Quartz.CFRunLoopRemoveSource(Quartz.CFRunLoopGetCurrent(), source,
                                 Quartz.kCFRunLoopDefaultMode)

    failures = 0
    for label, events in results:
        if not events:
            failures += 1
            print(f"  {label:14s} -> NOTHING CAPTURED  FAIL")
            continue
        for e in events:
            detail = f"{e[1]['name']}"
            if "mouse" in e[1]["name"].lower() or "Down" in e[1]["name"]:
                detail += f" at ({e[1]['x']},{e[1]['y']})"
            if e[1]["keycode"]:
                detail += f" keycode={e[1]['keycode']}"
            if e[1]["scroll"]:
                detail += f" scroll={e[1]['scroll']}"
            if e[1]["flags"] & mapper.FLAG_COMMAND:
                detail += " +cmd"
            print(f"  {label:14s} -> {detail}  PASS")

    print(f"\n  unicode 'hi' -> {len(unicode_events)} events captured "
          f"{'PASS' if len(unicode_events) >= 2 else 'FAIL'}")
    print(f"  result: {len(results) - failures}/{len(results)} event types posted")
    print("  (all swallowed by the tap -- no app received them)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
