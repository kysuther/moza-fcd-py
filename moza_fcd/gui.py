"""Desktop emulator of the MB7F FCD (737-style CDU) for developing without hardware.

    python -m moza_fcd gui                      # built-in demo app
    python -m moza_fcd gui --app my_app.py      # your app: a file defining main(screen)

The window draws whatever the *simulated device* is displaying - your app talks to
it over the full serial protocol, exactly as it would to real hardware. Clicks and
PC keyboard presses become key events (FcdScreen.on_key); status lights and brightness
reach the simulated device as the same main-board commands real hardware gets.

PC keyboard: letters/digits type, Space=SP, -=+/-, Backspace/Esc=CLR (hold to clear all),
Delete=DEL, Enter=EXEC, PgUp/PgDn=PREV/NEXT PAGE, F1-F6=LSK 1L-6L, F7-F12=LSK 1R-6R.
"""

from __future__ import annotations

import threading
import tkinter as tk
import tkinter.font as tkfont
from typing import Callable, Dict, List, Optional, Tuple

from . import keys as K
from .device import FcdScreen
from .page import COLUMNS, ROWS
from .session import SettingsChannel

# Screen palette (colour code -> hex). Code 0 is unknown on hardware; drawn dark grey here.
PALETTE = {0: "#3a3a3a", 1: "#3fe0ff", 2: "#ff4b4b", 3: "#ffe24a", 4: "#4dff7a",
           5: "#ff6bff", 6: "#ffb321", 7: "#f2f2f2", 8: "#8c8c8c"}

BEZEL_TOP = "#4b4f55"
BEZEL_BOTTOM = "#3a3d42"
BEZEL_EDGE = "#2a2c30"
KEY_FACE = "#1c1d20"
KEY_FACE_DOWN = "#4d5058"
KEY_EDGE = "#0c0c0d"
KEY_TEXT = "#ececec"
LIGHT_OFF = "#2a2410"
LIGHT_ON = "#ffb000"
LIGHT_TEXT_OFF = "#5c533a"

# Geometry
CW, RH = 18, 26                      # screen cell width / row height
SX, SY = 84, 44                      # screen origin (top-left of cell 0,0)
SW, SH = CW * COLUMNS, RH * ROWS
WIDTH = SX * 2 + SW
KX = 30                              # keypad x offset
ROW_Y = [452 + i * 46 for i in range(8)]
HEIGHT = ROW_Y[-1] + 58


def _rounded(c: tk.Canvas, x1, y1, x2, y2, r, **kw) -> int:
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class CduEmulator:
    def __init__(self, root: tk.Tk, screen: FcdScreen) -> None:
        self.root = root
        self.screen = screen
        self.sim = screen.simulator
        root.title("MOZA MB7F FCD - simulator")
        root.configure(bg=BEZEL_EDGE)
        root.resizable(False, False)

        family = "Consolas" if "Consolas" in tkfont.families() else "Courier"
        self.font_large = tkfont.Font(family=family, size=16, weight="bold")
        self.font_small = tkfont.Font(family=family, size=12)
        self.font_key = tkfont.Font(family="Arial", size=8, weight="bold")
        self.font_alpha = tkfont.Font(family="Arial", size=12, weight="bold")
        self.font_light = tkfont.Font(family="Arial", size=7, weight="bold")

        self.c = tk.Canvas(root, width=WIDTH, height=HEIGHT, bg=BEZEL_EDGE, highlightthickness=0)
        self.c.pack()
        self.status = tk.Label(root, anchor="w", bg=BEZEL_EDGE, fg="#b8b8b8", font=("Segoe UI", 9))
        self.status.pack(fill="x", padx=8, pady=(0, 6))

        self.keys: Dict[str, Tuple[int, int]] = {}   # name -> (face item, text item)
        self.lights: Dict[str, Tuple[int, int]] = {}
        self.cells: List[List[int]] = []
        self._shown: List[List[Optional[tuple]]] = [[None] * COLUMNS for _ in range(ROWS)]
        self._mouse_key: Optional[str] = None
        self._kbd_down: set = set()
        self._last_key = "-"

        self._draw_bezel()
        self._draw_screen()
        self._draw_lsks()
        self._draw_keypad()
        self._draw_lights()

        self.c.bind("<ButtonRelease-1>", self._mouse_up)
        root.bind("<KeyPress>", self._kbd_press)
        root.bind("<KeyRelease>", self._kbd_release)
        self._poll()

    # --- drawing ---------------------------------------------------------------------

    def _draw_bezel(self) -> None:
        c = self.c
        _rounded(c, 6, 6, WIDTH - 6, ROW_Y[0] - 30, 18, fill=BEZEL_TOP, outline="#5a5e65")
        _rounded(c, 6, ROW_Y[0] - 36, WIDTH - 6, HEIGHT - 6, 18, fill=BEZEL_BOTTOM, outline="#4a4e54")
        for x, y in ((24, 24), (WIDTH - 24, 24), (24, ROW_Y[0] - 52), (WIDTH - 24, ROW_Y[0] - 52),
                     (24, HEIGHT - 24), (WIDTH - 24, HEIGHT - 24)):
            c.create_oval(x - 8, y - 8, x + 8, y + 8, fill="#222326", outline="#666a70")
            c.create_line(x - 5, y, x + 5, y, fill="#666a70", width=2)
        for x in (28, WIDTH - 28):  # the two ambient-light sensors beside the keypad
            c.create_oval(x - 5, ROW_Y[2] + 12, x + 5, ROW_Y[2] + 22, fill="#d07a1a", outline="#7a4510")

    def _draw_screen(self) -> None:
        c = self.c
        _rounded(c, SX - 14, SY - 14, SX + SW + 14, SY + SH + 14, 10, fill="#141517", outline="#2e3034")
        c.create_rectangle(SX - 6, SY - 6, SX + SW + 6, SY + SH + 6, fill="#000000", outline="#000000")
        for r in range(ROWS):
            row = []
            for col in range(COLUMNS):
                row.append(c.create_text(SX + col * CW + CW / 2, SY + r * RH + RH - 3, text="",
                                         anchor="s", font=self.font_large, fill=PALETTE[7]))
            self.cells.append(row)
        self.overlay = c.create_text(SX + SW / 2, SY + SH / 2, text="", fill="#6f6f6f",
                                     font=self.font_small, justify="center")

    def _key(self, name: str, x1, y1, x2, y2, label: str, font=None, radius=6, oval=False) -> None:
        c = self.c
        tag = f"key:{name}"
        if oval:
            face = c.create_oval(x1, y1, x2, y2, fill=KEY_FACE, outline=KEY_EDGE, width=2, tags=tag)
        else:
            face = _rounded(c, x1, y1, x2, y2, radius, fill=KEY_FACE, outline=KEY_EDGE, width=2, tags=tag)
        text = c.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=label, fill=KEY_TEXT,
                             font=font or self.font_key, justify="center", tags=tag)
        c.tag_bind(tag, "<ButtonPress-1>", lambda e, n=name: self._mouse_down(n))
        self.keys[name] = (face, text)

    def _draw_lsks(self) -> None:
        for n in range(1, 7):
            y = SY + (2 * n) * RH + RH / 2 - 2
            self._key(f"LSK{n}L", 22, y - 10, 58, y + 10, "", radius=4)
            self._key(f"LSK{n}R", WIDTH - 58, y - 10, WIDTH - 22, y + 10, "", radius=4)
            self.c.create_line(58, y, SX - 14, y, fill="#6a6e75")
            self.c.create_line(SX + SW + 14, y, WIDTH - 58, y, fill="#6a6e75")

    def _draw_keypad(self) -> None:
        x0 = 60 + KX
        fw, fh, pitch = 50, 34, 58
        rows_fn = [("INIT_REF", "INIT\nREF"), ("RTE", "RTE"), ("CLB", "CLB"), ("CRZ", "CRZ"), ("DES", "DES")]
        rows_fn2 = [("MENU", "MENU"), ("LEGS", "LEGS"), ("DEP_ARR", "DEP\nARR"), ("HOLD", "HOLD"), ("PROG", "PROG")]
        for i, (name, label) in enumerate(rows_fn):
            self._key(name, x0 + i * pitch, ROW_Y[0], x0 + i * pitch + fw, ROW_Y[0] + fh, label)
        for i, (name, label) in enumerate(rows_fn2):
            self._key(name, x0 + i * pitch, ROW_Y[1], x0 + i * pitch + fw, ROW_Y[1] + fh, label)

        # BRT rocker and EXEC (with its light bar)
        bx = x0 + 5 * pitch + 14
        self._key("BRT-", bx, ROW_Y[0] + 4, bx + 38, ROW_Y[0] + fh - 4, "- BRT", radius=10)
        self._key("BRT+", bx + 38, ROW_Y[0] + 4, bx + 76, ROW_Y[0] + fh - 4, "+", radius=10)
        ex = bx + 14
        self.exec_bar = self.c.create_rectangle(ex + 6, ROW_Y[1] - 8, ex + 44, ROW_Y[1] - 3,
                                                fill=LIGHT_OFF, outline="#000")
        self._key("EXEC", ex, ROW_Y[1], ex + fw, ROW_Y[1] + fh, "EXEC")

        for r, pair in ((2, (("N1_LIMIT", "N1\nLIMIT"), ("FIX", "FIX"))),
                        (3, (("PREV_PAGE", "PREV\nPAGE"), ("NEXT_PAGE", "NEXT\nPAGE")))):
            for i, (name, label) in enumerate(pair):
                self._key(name, x0 + i * pitch, ROW_Y[r], x0 + i * pitch + fw, ROW_Y[r] + fh, label)

        k, kp = 36, 44
        nx = x0 + 10
        numpad = [("1", "2", "3"), ("4", "5", "6"), ("7", "8", "9"), (".", "0", "+/-")]
        for r, keys in enumerate(numpad):
            for i, name in enumerate(keys):
                x = nx + i * kp
                self._key(name, x, ROW_Y[4 + r], x + k, ROW_Y[4 + r] + k, name,
                          font=self.font_alpha if len(name) == 1 else self.font_key, oval=True)

        ax = x0 + 3 * pitch + 30
        alpha = ["ABCDE", "FGHIJ", "KLMNO", "PQRST", "UVWXY", ("Z", "SP", "DEL", "/", "CLR")]
        for r, keys in enumerate(alpha):
            for i, name in enumerate(keys):
                x = ax + i * kp
                self._key(name, x, ROW_Y[2 + r], x + k, ROW_Y[2 + r] + k, name,
                          font=self.font_alpha if len(name) == 1 else self.font_key)

    def _draw_lights(self) -> None:
        # MB7F annunciators (per MOZA Cockpit): CALL/FAIL on the left, MSG/OFST on the right.
        for name, x, r in (("CALL", 30, 3.6), ("FAIL", 30, 5.6), ("MSG", WIDTH - 30, 3.6), ("OFST", WIDTH - 30, 5.6)):
            y = ROW_Y[0] + r * 46
            box = self.c.create_rectangle(x - 9, y - 32, x + 9, y + 32, fill=LIGHT_OFF, outline="#000")
            txt = self.c.create_text(x, y, text=name, angle=90, fill=LIGHT_TEXT_OFF, font=self.font_light)
            self.lights[name] = (box, txt)

    # --- input -----------------------------------------------------------------------

    def _set_key_visual(self, name: str, down: bool) -> None:
        if name in self.keys:
            self.c.itemconfigure(self.keys[name][0], fill=KEY_FACE_DOWN if down else KEY_FACE)

    def _press(self, name: str) -> None:
        self._last_key = name
        self._set_key_visual(name, True)
        self.screen.emit_key(name, True)

    def _release(self, name: str) -> None:
        self._set_key_visual(name, False)
        self.screen.emit_key(name, False)

    def _mouse_down(self, name: str) -> None:
        self._mouse_key = name
        self._press(name)

    def _mouse_up(self, _event) -> None:
        if self._mouse_key:
            self._release(self._mouse_key)
            self._mouse_key = None

    def _kbd_press(self, event) -> None:
        name = K.KEYBOARD_MAP.get(event.keysym)
        if name and name not in self._kbd_down:  # ignore OS auto-repeat
            self._kbd_down.add(name)
            self._press(name)

    def _kbd_release(self, event) -> None:
        name = K.KEYBOARD_MAP.get(event.keysym)
        if name and name in self._kbd_down:
            self._kbd_down.discard(name)
            self._release(name)

    # --- refresh from the simulated device ---------------------------------------------

    def _poll(self) -> None:
        sim = self.sim
        if sim is not None:
            mode1 = sim.display_mode == 1
            for r in range(ROWS):
                for col in range(COLUMNS):
                    want = (sim.text[r][col], sim.styles[r][col]) if mode1 else (" ", 0x87)
                    if self._shown[r][col] != want:
                        self._shown[r][col] = want
                        ch, st = want
                        self.c.itemconfigure(self.cells[r][col], text=ch,
                                             fill=PALETTE.get(st & 0x0F, PALETTE[7]),
                                             font=self.font_large if st & 0x80 else self.font_small)
            if not self.screen.ready:
                overlay = "CONNECTING..."
            elif not mode1:
                overlay = "DEVICE UI\n(display mode 0 - not simulated)"
            else:
                overlay = ""
            self.c.itemconfigure(self.overlay, text=overlay)

            for name, (box, txt) in self.lights.items():
                on = sim.lights.get(name, False)
                self.c.itemconfigure(box, fill=LIGHT_ON if on else LIGHT_OFF)
                self.c.itemconfigure(txt, fill="#2a1c00" if on else LIGHT_TEXT_OFF)
            self.c.itemconfigure(self.exec_bar, fill=LIGHT_ON if sim.lights.get("EXEC") else LIGHT_OFF)

            self.status.configure(text=f"{self.screen.state.value}  |  frames {sim.frames_applied}  "
                                       f"seq {sim.last_sequence}  |  last key {self._last_key}  |  "
                                       f"F1-F6 / F7-F12 = LSKs, Enter = EXEC, Esc = CLR")
        self.root.after(33, self._poll)


def _key_echo_app(screen: FcdScreen) -> None:
    """Default when no app is given: show which keys arrive."""
    from .page import Page
    log: List[str] = []

    def on_key(name: str, pressed: bool) -> None:
        if pressed:
            log.append(name)
            pg = Page().center(0, "KEY ECHO", "cyan")
            for i, k in enumerate(log[-11:]):
                pg.write(2 + i, 1, k, "green")
            screen.show(pg)

    screen.on_key = on_key
    screen.show(Page().center(0, "KEY ECHO", "cyan").center(6, "PRESS ANY KEY", "white"))


def run(app_main: Optional[Callable[[FcdScreen], None]] = None, settings_window_s: float = 0.5) -> None:
    """Open the emulator and run app_main(screen) on a background thread.

    app_main receives a connected FcdScreen(port="sim") and may block (e.g. CduApp.run()).
    The same function can later be run against hardware with `python -m moza_fcd run --app ...`.
    """
    # The real device needs ~4 s to report its settings; the sim doesn't, so skip the wait.
    SettingsChannel.COLLECTION_WINDOW_S = settings_window_s
    screen = FcdScreen(port="sim", on_error=lambda m: print(f"[error] {m}"))
    screen.connect()

    root = tk.Tk()
    CduEmulator(root, screen)
    closing = threading.Event()

    def worker() -> None:
        try:
            screen.wait_ready(10)
            (app_main or _key_echo_app)(screen)
        except Exception:
            if not closing.is_set():
                import traceback
                traceback.print_exc()

    threading.Thread(target=worker, name="FcdApp", daemon=True).start()

    def on_close() -> None:
        closing.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    try:
        root.mainloop()
    finally:
        closing.set()
        screen.close(timeout=1.0)
