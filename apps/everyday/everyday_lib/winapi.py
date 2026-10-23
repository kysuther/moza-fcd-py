"""Thin wrappers for the Windows features the app drives: keystrokes, the clipboard, session and power,
and the light/dark theme. Everything here acts on the PC only (nothing is sent to the FCD).

The parsers (parse_chord, parse_steps, parse_powercfg) are plain Python; the rest needs Windows.
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import time
from typing import List, Optional, Tuple

CREATE_NO_WINDOW = 0x08000000

# --- keystrokes ------------------------------------------------------------------------

VK = {"CTRL": 0x11, "CONTROL": 0x11, "SHIFT": 0x10, "ALT": 0x12, "WIN": 0x5B,
      "ENTER": 0x0D, "RETURN": 0x0D, "ESC": 0x1B, "ESCAPE": 0x1B, "TAB": 0x09, "SPACE": 0x20,
      "BACKSPACE": 0x08, "DELETE": 0x2E, "DEL": 0x2E, "INSERT": 0x2D, "HOME": 0x24, "END": 0x23,
      "PGUP": 0x21, "PAGEUP": 0x21, "PGDN": 0x22, "PAGEDOWN": 0x22,
      "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27, "PRINTSCREEN": 0x2C, "PAUSE": 0x13,
      "CAPSLOCK": 0x14, "NUMLOCK": 0x90, "SCROLLLOCK": 0x91, "APPS": 0x5D,
      "VOLUMEUP": 0xAF, "VOLUMEDOWN": 0xAE, "VOLUMEMUTE": 0xAD,
      "MEDIANEXT": 0xB0, "MEDIAPREV": 0xB1, "MEDIASTOP": 0xB2, "MEDIAPLAYPAUSE": 0xB3,
      "MINUS": 0xBD, "PLUS": 0xBB, "EQUALS": 0xBB, "COMMA": 0xBC, "PERIOD": 0xBE, "SLASH": 0xBF,
      "SEMICOLON": 0xBA, "QUOTE": 0xDE, "LBRACKET": 0xDB, "RBRACKET": 0xDD, "BACKSLASH": 0xDC,
      "BACKTICK": 0xC0}
VK.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
VK.update({str(d): 0x30 + d for d in range(10)})
VK.update({f"F{n}": 0x6F + n for n in range(1, 25)})
VK.update({f"NUMPAD{d}": 0x60 + d for d in range(10)})
MODIFIERS = {0x11, 0x10, 0x12, 0x5B}
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x2C, 0x5B, 0x5D, 0x90,
            0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}

KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
INPUT_KEYBOARD = 1


def parse_chord(text: str) -> Tuple[int, ...]:
    """'CTRL+SHIFT+M' -> virtual-key codes in press order. Raises ValueError for unknown keys."""
    names = [p.strip().upper() for p in str(text).split("+")]
    if not names or any(not n for n in names):
        raise ValueError(f"bad chord {text!r}")
    try:
        vks = tuple(VK[n] for n in names)
    except KeyError as e:
        raise ValueError(f"unknown key {e.args[0]!r} in {text!r}") from None
    if all(vk in MODIFIERS for vk in vks) and len(vks) > 1:
        raise ValueError(f"chord {text!r} has only modifiers")
    return vks


def parse_steps(keys) -> List[tuple]:
    """A chord string or a list of steps -> [("chord", vks) | ("wait", seconds)].

    Steps: "CTRL+C", "WAIT 0.5" (seconds, up to 10)."""
    items = [keys] if isinstance(keys, str) else list(keys or [])
    if not items:
        raise ValueError("no keys")
    steps = []
    for item in items:
        m = re.fullmatch(r"\s*WAIT\s+(\d+(?:\.\d+)?)\s*", str(item), re.IGNORECASE)
        if m:
            secs = float(m.group(1))
            if secs > 10:
                raise ValueError("WAIT is limited to 10 seconds")
            steps.append(("wait", secs))
        else:
            steps.append(("chord", parse_chord(item)))
    return steps


def _input_types():
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):  # only here so the union has the right size
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    return KEYBDINPUT, INPUT


_TYPES = None


def _send(events: List[Tuple[int, int, int]]) -> None:
    """events: (vk, scan, flags)."""
    global _TYPES
    if _TYPES is None:
        _TYPES = _input_types()
    KEYBDINPUT, INPUT = _TYPES
    arr = (INPUT * len(events))()
    for i, (vk, scan, flags) in enumerate(events):
        arr[i].type = INPUT_KEYBOARD
        arr[i].u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def send_chord(vks) -> None:
    """Press the keys in order, release them in reverse (like pressing a shortcut by hand)."""
    def ev(vk, up):
        return (vk, 0, (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0))
    _send([ev(vk, False) for vk in vks] + [ev(vk, True) for vk in reversed(vks)])


def run_steps(steps, sleep=time.sleep) -> None:
    for kind, value in steps:
        if kind == "wait":
            sleep(value)
        else:
            send_chord(value)
            sleep(0.03)


def type_text(text: str) -> None:
    """Type text into the focused window as Unicode characters; newlines press Enter."""
    events = []
    for ch in str(text).replace("\r\n", "\n"):
        if ch == "\n":
            events += [(0x0D, 0, 0), (0x0D, 0, KEYEVENTF_KEYUP)]
        elif ch == "\t":
            events += [(0x09, 0, 0), (0x09, 0, KEYEVENTF_KEYUP)]
        else:
            data = ch.encode("utf-16-le")
            for i in range(0, len(data), 2):      # surrogate pairs send two code units
                unit = int.from_bytes(data[i:i + 2], "little")
                events += [(0, unit, KEYEVENTF_UNICODE), (0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    if events:
        _send(events)


# --- clipboard -------------------------------------------------------------------------

CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
CLIPBOARD_READ_LIMIT = 4000     # characters; the screen shows far fewer
_clip_ready = False


def _clip_api():
    global _clip_ready
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    if not _clip_ready:
        vp, sz = ctypes.c_void_p, ctypes.c_size_t
        user32.OpenClipboard.argtypes = [vp]
        user32.GetClipboardData.restype = vp
        user32.GetClipboardData.argtypes = [ctypes.c_uint]
        user32.SetClipboardData.restype = vp
        user32.SetClipboardData.argtypes = [ctypes.c_uint, vp]
        kernel32.GlobalAlloc.restype = vp
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, sz]
        kernel32.GlobalLock.restype = vp
        kernel32.GlobalLock.argtypes = [vp]
        kernel32.GlobalUnlock.argtypes = [vp]
        kernel32.GlobalSize.restype = sz
        kernel32.GlobalSize.argtypes = [vp]
        kernel32.GlobalFree.argtypes = [vp]
        _clip_ready = True
    return user32, kernel32


def _open_clipboard(user32) -> bool:
    for _ in range(10):                  # another app may hold it for a moment
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def clipboard_sequence() -> int:
    return int(ctypes.windll.user32.GetClipboardSequenceNumber())


def get_clipboard_text() -> Optional[str]:
    """The clipboard's text ('' if it holds something else), or None if it couldn't be opened."""
    user32, kernel32 = _clip_api()
    if not _open_clipboard(user32):
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            chars = min(kernel32.GlobalSize(handle) // 2, CLIPBOARD_READ_LIMIT)
            return ctypes.wstring_at(ptr, chars).split("\0", 1)[0]
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    user32, kernel32 = _clip_api()
    data = str(text).encode("utf-16-le") + b"\0\0"
    if not _open_clipboard(user32):
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


def clear_clipboard() -> bool:
    user32, _ = _clip_api()
    if not _open_clipboard(user32):
        return False
    try:
        return bool(user32.EmptyClipboard())
    finally:
        user32.CloseClipboard()


# --- session and power -----------------------------------------------------------------

def _run(args, timeout=10) -> str:
    out = subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return out.stdout


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input on this PC (game controllers, like the FCD, don't count)."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    ctypes.windll.kernel32.GetTickCount.restype = ctypes.c_uint
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def lock() -> None:
    ctypes.windll.user32.LockWorkStation()


def monitors_off() -> None:
    """Turn the monitors off (they wake on mouse or keyboard input). Posted, so it can't hang."""
    HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER = 0xFFFF, 0x0112, 0xF170
    ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)


def suspend() -> None:
    ctypes.windll.powrprof.SetSuspendState(False, False, False)


def schedule_shutdown(kind: str, delay_s: int) -> None:
    """kind 'shutdown' or 'restart', after delay_s seconds; abort_shutdown() cancels it."""
    flag = {"shutdown": "/s", "restart": "/r"}[kind]
    _run(["shutdown", flag, "/t", str(int(delay_s))])


def abort_shutdown() -> None:
    _run(["shutdown", "/a"])


ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x1, 0x2


def set_keep_awake(on: bool) -> None:
    """Stop the PC and display from sleeping while on. Per-thread: call from one long-lived thread."""
    k = ctypes.windll.kernel32
    k.SetThreadExecutionState.restype = ctypes.c_uint
    k.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    k.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0))


POWERCFG_RE = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s+\((.+?)\)(\s*\*)?\s*$")


def parse_powercfg(text: str) -> List[dict]:
    plans = []
    for line in text.splitlines():
        m = POWERCFG_RE.search(line.strip())
        if m:
            plans.append({"guid": m.group(1).lower(), "name": m.group(2), "active": bool(m.group(3))})
    return plans


def power_plans() -> List[dict]:
    return parse_powercfg(_run(["powercfg", "/list"]))


def set_power_plan(guid: str) -> None:
    if not POWERCFG_RE.match(f"{guid} (x)"):
        raise ValueError(f"bad power plan id {guid!r}")
    _run(["powercfg", "/setactive", guid])


# --- theme -----------------------------------------------------------------------------

THEME_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def dark_mode() -> Optional[bool]:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_KEY) as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return None


def set_dark_mode(on: bool) -> None:
    """Switch apps and the taskbar between dark and light, like Settings > Personalization > Colors."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_KEY, 0, winreg.KEY_SET_VALUE) as k:
        for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 0 if on else 1)
    HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x001A, 0x0002
    result = ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "ImmersiveColorSet",
                                             SMTO_ABORTIFHUNG, 200, ctypes.byref(result))


# --- launching -------------------------------------------------------------------------

def launch(run: str, args=()) -> None:
    """Open an app, file, folder or URL. With args, run it as a program with those arguments."""
    target = os.path.expandvars(os.path.expanduser(run))
    if args:
        subprocess.Popen([target, *[os.path.expandvars(str(a)) for a in args]], close_fds=True)
    else:
        os.startfile(target)
