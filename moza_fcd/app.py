"""A small framework for CDU-style apps on the 14x24 screen.

Boeing CDU layout conventions it follows:

    row 0        title (centred), page number at the right ("1/2")
    rows 1..12   six lines; line n has a small label on row 2n-1 and data on row 2n,
                 next to line select keys LSKnL / LSKnR
    row 13       scratchpad - typed characters go here; LSKs usually "enter" it into a field

Subclass `CduPage`, register pages on a `CduApp`, and call `app.run()` (blocking) or
drive it yourself with `app.handle_key()` / `app.refresh()`.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional

from . import keys as K
from .page import COLUMNS, Page

LINE_COUNT = 6
SCRATCHPAD_ROW = 13
CLR_HOLD_S = 0.6  # holding CLR this long clears the whole scratchpad


def label_row(n: int) -> int:
    return 2 * n - 1


def data_row(n: int) -> int:
    return 2 * n


class CduPage:
    """One screen of an app. Override what you need."""

    title = ""
    page_count = 1  # >1 enables PREV/NEXT PAGE and the "1/2" indicator

    def __init__(self) -> None:
        self.page_index = 0

    def draw(self, app: "CduApp", pg: Page) -> None:
        """Draw rows 1-12. Use app.line() for the standard label/data layout."""

    def on_lsk(self, app: "CduApp", side: str, n: int) -> bool:
        """Handle LSKn (side 'L'/'R'). Return True if handled."""
        return False

    def on_key(self, app: "CduApp", name: str) -> bool:
        """Handle any other key before the app's defaults. Return True if handled."""
        return False

    def on_exec(self, app: "CduApp") -> None:
        """Called when EXEC is pressed while app.exec_pending is set."""

    def on_enter(self, app: "CduApp") -> None:
        """Called when the page becomes current."""


class CduApp:
    def __init__(self, screen, home: str = "", function_keys: Optional[Dict[str, str]] = None,
                 refresh_s: float = 0.5) -> None:
        """
        screen         an FcdScreen (or anything with show(), set_light(), on_key)
        home           name of the first page
        function_keys  e.g. {"INIT_REF": "index", "MENU": "menu"} - keys that jump to pages
        refresh_s      how often run() redraws, for pages showing live data
        """
        self.screen = screen
        self.pages: Dict[str, CduPage] = {}
        self.function_keys = dict(function_keys or {})
        self.refresh_s = refresh_s
        self.scratchpad = ""
        self.message = ""  # shown in the scratchpad until CLR, e.g. "INVALID ENTRY"
        self.exec_pending = False
        self.current: Optional[CduPage] = None
        self._home = home
        self._lock = threading.RLock()
        self._clr_down_at: Optional[float] = None
        self._stop = threading.Event()
        self._key_hooks: List[Callable[[str, bool], None]] = []
        screen.on_key = self.handle_key

    # --- page management -------------------------------------------------------------

    def add(self, name: str, page: CduPage) -> "CduApp":
        self.pages[name] = page
        return self

    def go(self, name: str, page_index: int = 0) -> None:
        with self._lock:
            page = self.pages[name]
            page.page_index = min(page_index, page.page_count - 1)
            self.current = page
            page.on_enter(self)
            self.refresh()

    # --- scratchpad helpers ------------------------------------------------------------

    def show_message(self, text: str) -> None:
        self.message = text.upper()[:COLUMNS]
        self.refresh()

    def take_scratchpad(self) -> str:
        """Return and clear the scratchpad (what an LSK 'enters' into a field)."""
        text, self.scratchpad = self.scratchpad, ""
        return text

    def set_exec_pending(self, pending: bool) -> None:
        self.exec_pending = pending
        self.refresh()

    # --- drawing -----------------------------------------------------------------------

    def line(self, pg: Page, n: int, left: str = "", right: str = "", left_label: str = "", right_label: str = "",
             color: str = "white", left_color: Optional[str] = None, right_color: Optional[str] = None,
             label_color: str = "white") -> None:
        """Standard CDU line n (1-6): small labels above, large data below."""
        if left_label:
            pg.write(label_row(n), 1, left_label, label_color, small=True)
        if right_label:
            pg.right(label_row(n), right_label + " ", label_color, small=True)
        if left:
            pg.write(data_row(n), 0, left, left_color or color)
        if right:
            pg.right(data_row(n), right, right_color or color)

    def render(self) -> Page:
        pg = Page()
        page = self.current
        if page is not None:
            pg.center(0, ("MOD " if self.exec_pending else "") + page.title, "white")
            if page.page_count > 1:
                pg.right(0, f"{page.page_index + 1}/{page.page_count}", "white", small=True)
            page.draw(self, pg)
        if self.message:
            pg.write(SCRATCHPAD_ROW, 0, self.message, "white")
        else:
            pg.write(SCRATCHPAD_ROW, 0, self.scratchpad[-COLUMNS:], "white")
        return pg

    def refresh(self) -> None:
        with self._lock:
            self.screen.show(self.render())
            self.screen.set_light("EXEC", self.exec_pending)
            self.screen.set_light("MSG", bool(self.message))

    # --- input -------------------------------------------------------------------------

    def add_key_hook(self, hook: Callable[[str, bool], None]) -> None:
        """Observe every key event (e.g. for logging)."""
        self._key_hooks.append(hook)

    def handle_key(self, name: str, pressed: bool) -> None:
        for hook in self._key_hooks:
            hook(name, pressed)
        with self._lock:
            if name == "CLR":
                self._handle_clr(pressed)
                return
            if not pressed:
                return
            self._handle_press(name)
            self.refresh()

    def _handle_clr(self, pressed: bool) -> None:
        if pressed:
            self._clr_down_at = time.monotonic()
            if self.message:
                self.message = ""
            elif self.scratchpad:
                self.scratchpad = self.scratchpad[:-1]
        else:
            if self._clr_down_at is not None and time.monotonic() - self._clr_down_at >= CLR_HOLD_S:
                self.scratchpad = ""
            self._clr_down_at = None
        self.refresh()

    def _handle_press(self, name: str) -> None:
        page = self.current
        if page is not None and page.on_key(self, name):
            return

        ch = K.typed_char(name)
        if ch is not None:
            self.message = ""
            if name == "+/-" and self.scratchpad.endswith("-"):
                self.scratchpad = self.scratchpad[:-1] + "+"
            elif len(self.scratchpad) < COLUMNS:
                self.scratchpad += ch
            return

        lsk = K.parse_lsk(name)
        if lsk is not None:
            if page is not None:
                page.on_lsk(self, *lsk)  # unhandled LSKs are silently ignored, as on a real CDU
            return

        if name == "DEL":
            if not self.scratchpad and not self.message:
                self.message = "DELETE"
        elif name == "EXEC":
            if self.exec_pending and page is not None:
                page.on_exec(self)
                self.exec_pending = False
        elif name in ("PREV_PAGE", "NEXT_PAGE") and page is not None and page.page_count > 1:
            step = 1 if name == "NEXT_PAGE" else -1
            page.page_index = (page.page_index + step) % page.page_count
        elif name in self.function_keys:
            self.go(self.function_keys[name])

    # --- running -----------------------------------------------------------------------

    def run(self) -> None:
        """Show the home page and keep redrawing (for live data) until stop() or Ctrl+C. Ctrl+C is passed
        on (KeyboardInterrupt) so `python -m moza_fcd run` can log why the app ended."""
        if self._home:
            self.go(self._home)
        while not self._stop.wait(self.refresh_s):
            self.refresh()

    def stop(self) -> None:
        self._stop.set()
