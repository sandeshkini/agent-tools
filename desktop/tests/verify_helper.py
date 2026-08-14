"""End-to-end proof: cptr client message -> mapper -> unix socket -> Swift helper
-> real CGEvent.

Same swallowing tap as verify_post.py, so the events are posted for real but
never delivered to any application. Only events carrying our tag are dropped;
the human's own input passes through untouched.
"""

from __future__ import annotations

import time

import Quartz

import client
import inject
import mapper
import probe_display

CAPTURED: list[dict] = []

WATCH = {
    Quartz.kCGEventLeftMouseDown: "leftDown",
    Quartz.kCGEventLeftMouseUp: "leftUp",
    Quartz.kCGEventRightMouseDown: "rightDown",
    Quartz.kCGEventScrollWheel: "scroll",
    Quartz.kCGEventKeyDown: "keyDown",
    Quartz.kCGEventKeyUp: "keyUp",
}
MASK = sum(Quartz.CGEventMaskBit(t) for t in WATCH)


def _callback(proxy, ev_type, event, refcon):
    if not inject.is_ours(event):
        return event  # never swallow real user input
    loc = Quartz.CGEventGetLocation(event)
    CAPTURED.append({
        "name": WATCH.get(ev_type, str(ev_type)),
        "x": round(loc.x, 1), "y": round(loc.y, 1),
        "keycode": int(Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode)),
        "clicks": int(Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGMouseEventClickState)),
        "cmd": bool(int(Quartz.CGEventGetFlags(event)) & mapper.FLAG_COMMAND),
    })
    return None


def main() -> int:
    d = [x for x in probe_display.displays() if x["main"]][0]
    display = mapper.Display(d["origin"][0], d["origin"][1], *d["points"])

    with client.InputClient() as c:
        if not c.trusted():
            print("  helper reports NOT trusted -- grant Accessibility to cptr-input.app")
            return 2

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault, MASK, _callback, None)
        if not tap:
            print("  could not create tap")
            return 2
        src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src,
                                  Quartz.kCFRunLoopDefaultMode)
        Quartz.CGEventTapEnable(tap, True)

        cases = [
            ("left click",   "pointer", {"event": "down", "x": 0.5, "y": 0.5,
                                         "normalized": True, "button": "left", "click_count": 1}),
            ("left release", "pointer", {"event": "up", "x": 0.5, "y": 0.5,
                                         "normalized": True, "button": "left", "click_count": 1}),
            ("right click",  "pointer", {"event": "down", "x": 0.3, "y": 0.3,
                                         "normalized": True, "button": "right", "click_count": 1}),
            ("scroll",       "wheel",   {"x": 0.5, "y": 0.5, "normalized": True, "delta_y": 120}),
            ("Cmd+C",        "key",     {"event": "keyDown", "key": "c", "code": "KeyC",
                                         "text": "", "modifiers": mapper.CDP_META}),
            ("type 'hi'",    "paste",   {"text": "hi"}),
        ]

        results, latencies = [], []
        for label, kind, data in cases:
            CAPTURED.clear()
            t0 = time.perf_counter()
            c.dispatch(display, kind, data)
            latencies.append((time.perf_counter() - t0) * 1000)
            Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.3, False)
            results.append((label, list(CAPTURED)))

        Quartz.CGEventTapEnable(tap, False)
        Quartz.CFRunLoopRemoveSource(Quartz.CFRunLoopGetCurrent(), src,
                                     Quartz.kCFRunLoopDefaultMode)

    failures = 0
    for (label, events), ms in zip(results, latencies):
        if not events:
            failures += 1
            print(f"  {label:14s} -> NOTHING CAPTURED  FAIL")
            continue
        summary = ", ".join(
            e["name"] + (f"@({e['x']},{e['y']})" if e["name"].endswith("Down") and e["keycode"] == 0 else "")
            + (f" key={e['keycode']}" if e["keycode"] else "")
            + (" +cmd" if e["cmd"] else "")
            for e in events)
        print(f"  {label:14s} -> {summary}   [{ms:.1f}ms]  PASS")

    print(f"\n  result: {len(results) - failures}/{len(results)} through the helper")
    print(f"  mean dispatch latency: {sum(latencies) / len(latencies):.1f}ms")
    print("  (all swallowed by the tap -- no app received them)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
