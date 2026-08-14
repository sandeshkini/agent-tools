"""Stage 1: actually post events to macOS.

Two tiers, deliberately separated by what macOS demands:

  warp  -- CGWarpMouseCursorPosition. Moves the real cursor. Needs NO permission.
           Enough to prove the coordinate pipeline end to end.
  post  -- CGEventPost. Clicks, scroll, keys. Requires Accessibility (TCC).
           Without the grant macOS drops these SILENTLY -- no error, no log.
           That silence is why `permission_status()` exists.
"""

from __future__ import annotations

import Quartz
from ApplicationServices import AXIsProcessTrusted

import mapper

# Every event we post carries this tag in its userData field, so a verification
# tap can drop OUR events while letting the human's real input pass through
# untouched. Without it an active tap swallows the user's own keystrokes.
EVENT_TAG = 0x43505452  # "CPTR"


def _tag(event) -> None:
    Quartz.CGEventSetIntegerValueField(event, Quartz.kCGEventSourceUserData, EVENT_TAG)


def is_ours(event) -> bool:
    return int(Quartz.CGEventGetIntegerValueField(
        event, Quartz.kCGEventSourceUserData)) == EVENT_TAG


def permission_status() -> bool:
    """True if this process may post synthetic events. Never prompts."""
    return bool(AXIsProcessTrusted())


def cursor_position() -> tuple[float, float]:
    """Where the cursor actually is, in points. Used to verify a warp landed."""
    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return float(loc.x), float(loc.y)


def warp(x: float, y: float) -> None:
    """Move the cursor. No permission required."""
    Quartz.CGWarpMouseCursorPosition(Quartz.CGPointMake(x, y))


def post_mouse(ev_type: int, x: float, y: float, clicks: int = 1, flags: int = 0) -> None:
    event = Quartz.CGEventCreateMouseEvent(None, ev_type, Quartz.CGPointMake(x, y), 0)
    if clicks:
        Quartz.CGEventSetIntegerValueField(event, Quartz.kCGMouseEventClickState, clicks)
    if flags:
        Quartz.CGEventSetFlags(event, flags)
    _tag(event)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def post_scroll(dy: float, dx: float = 0.0, flags: int = 0) -> None:
    event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitPixel,
                                                 2, int(dy), int(dx))
    if flags:
        Quartz.CGEventSetFlags(event, flags)
    _tag(event)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def post_key(keycode: int, down: bool, flags: int = 0) -> None:
    event = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
    if flags:
        Quartz.CGEventSetFlags(event, flags)
    _tag(event)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def post_unicode(text: str) -> None:
    """Type text without touching the keycode table -- layout independent."""
    for chunk in text:
        event = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
        Quartz.CGEventKeyboardSetUnicodeString(event, len(chunk), chunk)
        _tag(event)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
        Quartz.CGEventKeyboardSetUnicodeString(up, len(chunk), chunk)
        _tag(up)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


# --- flag name -> bits, for replaying PlannedEvent output -------------------
_FLAG_BITS = {
    "cmd": mapper.FLAG_COMMAND, "shift": mapper.FLAG_SHIFT,
    "alt": mapper.FLAG_ALTERNATE, "ctrl": mapper.FLAG_CONTROL,
}


def _bits(desc: str) -> int:
    if not desc or desc == "none":
        return 0
    return sum(_FLAG_BITS.get(n, 0) for n in desc.split("+"))


def execute(event: mapper.PlannedEvent) -> None:
    """Post one PlannedEvent produced by mapper.plan()."""
    d = event.detail
    if event.kind == "mouse":
        post_mouse(d["type"], d["x"], d["y"], d.get("clicks", 0), _bits(d.get("flags", "")))
    elif event.kind == "scroll":
        post_scroll(d["dy"], d["dx"], _bits(d.get("flags", "")))
    elif event.kind == "key":
        post_key(d["keycode"], d["down"], _bits(d.get("flags", "")))
    elif event.kind == "unicode":
        post_unicode(d["text"])
    elif event.kind == "UNMAPPED":
        raise KeyError(f"no keycode for {d.get('code')!r}")
