"""Translate cptr viewer input events into macOS CGEvent parameters.

Pure translation only -- nothing here posts an event. `inject.py` (stage 1) is the
only module allowed to touch the OS, so this file can be exercised with no TCC
permissions at all.

Input shapes mirror cptr/utils/browser/viewer.py:1239-1290 exactly:
    pointer  {event: move|down|up, x, y, normalized, button, buttons, click_count, modifiers}
    wheel    {x, y, normalized, delta_x, delta_y, modifiers}
    key      {event: keyDown|keyUp|char, key, code, text, modifiers, ...}
    paste    {text}
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- CDP modifier bits (what the cptr client sends) -------------------------
CDP_ALT, CDP_CTRL, CDP_META, CDP_SHIFT = 1, 2, 4, 8

# --- CGEventFlags -----------------------------------------------------------
FLAG_SHIFT = 1 << 17
FLAG_CONTROL = 1 << 18
FLAG_ALTERNATE = 1 << 19
FLAG_COMMAND = 1 << 20

# --- CGEventType ------------------------------------------------------------
LEFT_DOWN, LEFT_UP = 1, 2
RIGHT_DOWN, RIGHT_UP = 3, 4
MOUSE_MOVED = 5
LEFT_DRAG, RIGHT_DRAG = 6, 7
OTHER_DOWN, OTHER_UP, OTHER_DRAG = 25, 26, 27

# --- macOS virtual keycodes (kVK_*) ----------------------------------------
# Only what shortcuts and navigation need. Printable text goes through
# CGEventKeyboardSetUnicodeString instead, so this table stays small and is
# immune to keyboard-layout differences.
KEYCODES: dict[str, int] = {
    # letters -- required for Cmd-C, Cmd-V, Cmd-Tab style shortcuts
    "KeyA": 0, "KeyS": 1, "KeyD": 2, "KeyF": 3, "KeyH": 4, "KeyG": 5,
    "KeyZ": 6, "KeyX": 7, "KeyC": 8, "KeyV": 9, "KeyB": 11, "KeyQ": 12,
    "KeyW": 13, "KeyE": 14, "KeyR": 15, "KeyY": 16, "KeyT": 17, "KeyO": 31,
    "KeyU": 32, "KeyI": 34, "KeyP": 35, "KeyL": 37, "KeyJ": 38, "KeyK": 40,
    "KeyN": 45, "KeyM": 46,
    # digits
    "Digit1": 18, "Digit2": 19, "Digit3": 20, "Digit4": 21, "Digit6": 22,
    "Digit5": 23, "Digit9": 25, "Digit7": 26, "Digit8": 28, "Digit0": 29,
    # punctuation
    "Equal": 24, "Minus": 27, "BracketRight": 30, "BracketLeft": 33,
    "Quote": 39, "Semicolon": 41, "Backslash": 42, "Comma": 43,
    "Slash": 44, "Period": 47, "Backquote": 50,
    # editing / navigation
    "Enter": 36, "Tab": 48, "Space": 49, "Backspace": 51, "Escape": 53,
    "Delete": 117, "Home": 115, "End": 119, "PageUp": 116, "PageDown": 121,
    "ArrowLeft": 123, "ArrowRight": 124, "ArrowDown": 125, "ArrowUp": 126,
    # modifiers
    "MetaLeft": 55, "ShiftLeft": 56, "CapsLock": 57, "AltLeft": 58,
    "ControlLeft": 59, "ShiftRight": 60, "AltRight": 61, "ControlRight": 62,
    # function row
    "F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97,
    "F7": 98, "F8": 100, "F9": 101, "F10": 109, "F11": 103, "F12": 111,
}


@dataclass
class Display:
    """Bounds of the captured display, in points (the unit CGEvent uses)."""

    x: float = 0.0
    y: float = 0.0
    width: float = 1728.0
    height: float = 1117.0

    def to_global(self, nx: float, ny: float) -> tuple[float, float]:
        """Normalized 0..1 frame coords -> global screen point.

        Retina cancels out here: the client sends a fraction of the frame, so the
        physical pixel size of the capture never enters the maths.
        """
        return self.x + nx * self.width, self.y + ny * self.height


@dataclass
class PlannedEvent:
    """A CGEvent that inject.py would post. Printable, comparable, testable."""

    kind: str
    detail: dict = field(default_factory=dict)

    def __str__(self) -> str:
        bits = ", ".join(f"{k}={v}" for k, v in self.detail.items())
        return f"{self.kind}({bits})"


def flags_from_cdp(modifiers: int) -> int:
    """CDP modifier bitmask -> CGEventFlags."""
    flags = 0
    if modifiers & CDP_SHIFT:
        flags |= FLAG_SHIFT
    if modifiers & CDP_CTRL:
        flags |= FLAG_CONTROL
    if modifiers & CDP_ALT:
        flags |= FLAG_ALTERNATE
    if modifiers & CDP_META:
        flags |= FLAG_COMMAND
    return flags


def describe_flags(flags: int) -> str:
    names = []
    for bit, name in (
        (FLAG_COMMAND, "cmd"), (FLAG_SHIFT, "shift"),
        (FLAG_ALTERNATE, "alt"), (FLAG_CONTROL, "ctrl"),
    ):
        if flags & bit:
            names.append(name)
    return "+".join(names) or "none"


def _point(display: Display, data: dict) -> tuple[float, float] | None:
    """Mirror of viewer.py _coordinates(), retargeted at the screen."""
    x, y = float(data.get("x", -1)), float(data.get("y", -1))
    if data.get("normalized"):
        if not (0 <= x <= 1 and 0 <= y <= 1):
            return None
        return display.to_global(x, y)
    # Absolute frame coords: caller must supply frame size to scale from.
    fw = float(data.get("frame_width", 0)) or display.width
    fh = float(data.get("frame_height", 0)) or display.height
    if not (0 <= x <= fw and 0 <= y <= fh):
        return None
    return display.to_global(x / fw, y / fh)


def plan_pointer(display: Display, data: dict, buttons_held: int = 0) -> list[PlannedEvent]:
    pt = _point(display, data)
    if pt is None:
        return []
    x, y = pt
    event = str(data.get("event", ""))
    button = str(data.get("button", "none"))
    flags = flags_from_cdp(int(data.get("modifiers", 0)))
    clicks = max(0, min(3, int(data.get("click_count", 0))))

    if event == "move":
        # A move while a button is held must be a *drag* event type, or macOS
        # will not track text selection or window drags.
        if buttons_held & 1:
            kind = LEFT_DRAG
        elif buttons_held & 2:
            kind = RIGHT_DRAG
        elif buttons_held:
            kind = OTHER_DRAG
        else:
            kind = MOUSE_MOVED
        return [PlannedEvent("mouse", {"type": kind, "x": round(x, 1), "y": round(y, 1),
                                       "flags": describe_flags(flags)})]

    down = event == "down"
    if button == "left":
        kind = LEFT_DOWN if down else LEFT_UP
    elif button == "right":
        kind = RIGHT_DOWN if down else RIGHT_UP
    elif button == "none":
        return []
    else:
        kind = OTHER_DOWN if down else OTHER_UP

    return [PlannedEvent("mouse", {"type": kind, "x": round(x, 1), "y": round(y, 1),
                                   "clicks": clicks, "flags": describe_flags(flags)})]


def plan_wheel(display: Display, data: dict) -> list[PlannedEvent]:
    pt = _point(display, data)
    if pt is None:
        return []
    # macOS scrolls opposite to DOM deltas.
    return [PlannedEvent("scroll", {
        "dy": -float(data.get("delta_y", 0)),
        "dx": -float(data.get("delta_x", 0)),
        "at": (round(pt[0], 1), round(pt[1], 1)),
        "flags": describe_flags(flags_from_cdp(int(data.get("modifiers", 0)))),
    })]


def plan_key(data: dict) -> list[PlannedEvent]:
    event = str(data.get("event", ""))
    if event not in {"keyDown", "keyUp", "char"}:
        return []
    code = str(data.get("code", ""))
    text = str(data.get("text", ""))
    modifiers = int(data.get("modifiers", 0))
    flags = flags_from_cdp(modifiers)

    # Printable text with no command modifier -> unicode injection. Avoids the
    # keycode table entirely, so layouts/accents/emoji all work.
    shortcut = bool(modifiers & (CDP_META | CDP_CTRL))
    if event == "char" or (text and text.isprintable() and not shortcut):
        if event == "keyUp":
            return []
        return [PlannedEvent("unicode", {"text": text})]

    keycode = KEYCODES.get(code)
    if keycode is None:
        return [PlannedEvent("UNMAPPED", {"code": code, "key": data.get("key", "")})]
    return [PlannedEvent("key", {"keycode": keycode, "code": code,
                                 "down": event == "keyDown",
                                 "flags": describe_flags(flags)})]


def plan_paste(data: dict) -> list[PlannedEvent]:
    return [PlannedEvent("unicode", {"text": str(data.get("text", ""))})]


def plan(display: Display, kind: str, data: dict, buttons_held: int = 0) -> list[PlannedEvent]:
    if kind == "pointer":
        return plan_pointer(display, data, buttons_held)
    if kind == "wheel":
        return plan_wheel(display, data)
    if kind == "key":
        return plan_key(data)
    if kind in {"paste", "text"}:
        return plan_paste(data)
    return []
