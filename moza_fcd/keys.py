"""Canonical key names for the MB7F (Boeing 737-style CDU) keypad.

Apps receive these strings through `FcdScreen.on_key(name, pressed)`, whether the
press came from the sim GUI or (once the HID layout is mapped) the real device.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

LSK_LEFT = tuple(f"LSK{n}L" for n in range(1, 7))
LSK_RIGHT = tuple(f"LSK{n}R" for n in range(1, 7))

FUNCTION_KEYS = (
    "INIT_REF", "RTE", "CLB", "CRZ", "DES",
    "MENU", "LEGS", "DEP_ARR", "HOLD", "PROG",
    "N1_LIMIT", "FIX", "PREV_PAGE", "NEXT_PAGE",
    "EXEC", "BRT-", "BRT+",
)

LETTERS = tuple(chr(c) for c in range(ord("A"), ord("Z") + 1))
DIGITS = tuple(str(d) for d in range(10))
SYMBOLS = (".", "+/-", "/", "SP", "DEL", "CLR")

ALL_KEYS = LSK_LEFT + LSK_RIGHT + FUNCTION_KEYS + LETTERS + DIGITS + SYMBOLS

# Status lights, in the order MOZA Cockpit lists them for the T23 CDU (cdu_EXEC, cdu_MSG,
# cdu_FAIL, cdu_CALL, cdu_OFST). EXEC is the bar above the EXEC key; CALL/FAIL sit on the
# left edge and MSG/OFST on the right, as on a 737NG CDU.
LIGHTS = ("EXEC", "MSG", "FAIL", "CALL", "OFST")

# CD_LedEnable index per light, from a scan on a real MB7F (indices 5-20 light nothing).
LED_IDS: Dict[str, int] = {"CALL": 0, "FAIL": 1, "EXEC": 2, "MSG": 3, "OFST": 4}

# MB7F ("T23 CDU") HID joystick button number -> key name. Taken from MOZA Cockpit's own
# key-test page for the device (button numbers placed on the panel image), matched to the
# keypad layout. Button numbers are HID button usages, starting at 1.
MB7F_HID_BUTTONS: Dict[int, str] = {
    **{43 + i: f"LSK{i + 1}L" for i in range(6)},
    **{49 + i: f"LSK{i + 1}R" for i in range(6)},
    57: "INIT_REF", 58: "RTE", 59: "CLB", 60: "CRZ", 61: "DES",
    62: "MENU", 63: "LEGS", 64: "DEP_ARR", 65: "HOLD", 66: "PROG", 71: "EXEC",
    67: "N1_LIMIT", 68: "FIX", 69: "PREV_PAGE", 70: "NEXT_PAGE",
    56: "BRT-", 55: "BRT+",
    **{13 + i: chr(ord("A") + i) for i in range(25)},      # A..Y
    38: "Z", 39: "SP", 40: "DEL", 41: "/", 42: "CLR",
    **{1 + i: str(i + 1) for i in range(9)},               # 1..9
    10: ".", 11: "0", 12: "+/-",
}

# HID axes on the MB7F: two ambient-light sensors (the small dots beside the keypad), 0-65535.
MB7F_HID_AXES = {"Rx": "LIGHT_SENSOR_A", "Ry": "LIGHT_SENSOR_B", "Z": "LIGHT_SENSOR_AB"}


def parse_lsk(name: str) -> Optional[Tuple[str, int]]:
    """'LSK3R' -> ('R', 3); anything else -> None."""
    if len(name) == 5 and name.startswith("LSK") and name[3].isdigit() and name[4] in "LR":
        return name[4], int(name[3])
    return None


def typed_char(name: str) -> Optional[str]:
    """The character a key types into the scratchpad, or None for non-typing keys."""
    if name in LETTERS or name in DIGITS or name in (".", "/"):
        return name
    if name == "SP":
        return " "
    if name == "+/-":
        return "-"
    return None


# PC keyboard -> key name, used by the sim GUI. Keys are Tk keysyms.
KEYBOARD_MAP: Dict[str, str] = {
    **{c.lower(): c for c in LETTERS},
    **{c: c for c in LETTERS},
    **{d: d for d in DIGITS},
    "period": ".", "slash": "/", "minus": "+/-", "plus": "+/-", "space": "SP",
    "BackSpace": "CLR", "Delete": "DEL", "Escape": "CLR",
    "Return": "EXEC", "KP_Enter": "EXEC",
    "Prior": "PREV_PAGE", "Next": "NEXT_PAGE",
    **{f"F{n}": f"LSK{n}L" for n in range(1, 7)},
    **{f"F{n + 6}": f"LSK{n}R" for n in range(1, 7)},
    **{f"KP_{d}": d for d in DIGITS},
    "KP_Decimal": ".", "KP_Divide": "/", "KP_Subtract": "+/-",
}
