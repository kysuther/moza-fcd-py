"""Demo CDU app, modelled on the 737 INIT/REF INDEX. Shows off the app framework:

* INDEX    - LSK navigation (the page from the reference photo)
* IDENT    - static info
* POS      - live data (UTC clock), PREV/NEXT across 2 pages
* PERF     - scratchpad entry with validation, MOD title + EXEC light, ERASE
* NOTES    - a tiny notepad: type, then LSK a line to store it (DEL then LSK erases)
* MENU     - top-level menu

Runs anywhere an FcdScreen does:  python -m moza_fcd gui   /   python -m moza_fcd run --app moza_fcd/demo_app.py
"""

from __future__ import annotations

import platform
import re
import time

from . import __version__
from .app import CduApp, CduPage
from .page import Page


class IndexPage(CduPage):
    title = "INIT/REF INDEX"
    targets = {("L", 1): "ident", ("L", 2): "pos", ("L", 3): "perf", ("R", 1): "notes", ("R", 6): "menu"}

    def draw(self, app: CduApp, pg: Page) -> None:
        app.line(pg, 1, "<IDENT", "NOTES>")
        app.line(pg, 2, "<POS", "MSG RECALL>", right_color="grey")
        app.line(pg, 3, "<PERF", "ALTN DEST>", right_color="grey")
        app.line(pg, 4, "<TAKEOFF", left_color="grey")
        app.line(pg, 5, "<APPROACH", "SEL CONFIG>", left_color="grey", right_color="grey")
        app.line(pg, 6, "<OFFSET", "MENU>", left_color="grey")

    def on_lsk(self, app: CduApp, side: str, n: int) -> bool:
        target = self.targets.get((side, n))
        if target:
            app.go(target)
        else:
            app.show_message("NOT IN DEMO")
        return True


class IdentPage(CduPage):
    title = "IDENT"

    def draw(self, app: CduApp, pg: Page) -> None:
        app.line(pg, 1, "MB7F-SIM", "PYTHON", left_label="MODEL", right_label="ENG RATING")
        app.line(pg, 2, f"MOZA_FCD {__version__}", left_label="NAV DATA")
        app.line(pg, 3, platform.system().upper()[:12], left_label="HOST OS")
        app.line(pg, 4, platform.python_version(), left_label="PYTHON")
        app.line(pg, 5, platform.node().upper()[:20], left_label="HOSTNAME")
        pg.fill(11, "-", "white", small=True)
        app.line(pg, 6, "<INDEX", "POS INIT>")

    def on_lsk(self, app: CduApp, side: str, n: int) -> bool:
        if n == 6:
            app.go("index" if side == "L" else "pos")
            return True
        return False


class PosPage(CduPage):
    title = "POS INIT"
    page_count = 2

    def draw(self, app: CduApp, pg: Page) -> None:
        now = time.gmtime()
        if self.page_index == 0:
            app.line(pg, 1, "N47°27.0 W122°18.5", left_label="LAST POS", color="green")
            app.line(pg, 2, "KSEA", left_label="REF AIRPORT")
            app.line(pg, 3, "----", left_label="GATE")
            app.line(pg, 4, time.strftime("%H%M.%S", now) + "Z", "--------",
                     left_label="GMT-MON/DY", right_label="SET IRS POS", left_color="green")
            app.line(pg, 5, time.strftime("%m/%d", now), left_color="green")
        else:
            app.line(pg, 1, "N47°27.0 W122°18.5", left_label="FMC POS", color="green")
            app.line(pg, 2, "N47°27.0 W122°18.5", left_label="IRS L", color="green")
            app.line(pg, 3, "N47°27.0 W122°18.5", left_label="IRS R", color="green")
            app.line(pg, 4, f"{int(time.time()) % 60:02d}.0", left_label="GPS SEC (LIVE)", color="magenta")
        pg.fill(11, "-", "white", small=True)
        app.line(pg, 6, "<INDEX", "ROUTE>")

    def on_lsk(self, app: CduApp, side: str, n: int) -> bool:
        if n == 6 and side == "L":
            app.go("index")
            return True
        if n == 6 and side == "R":
            app.show_message("NOT IN DEMO")
            return True
        return False


class PerfPage(CduPage):
    """Editable fields with validation and the MOD/EXEC flow."""

    title = "PERF INIT"
    fields = {
        1: ("GW/CRZ CG", "gw", r"^\d{2,3}(\.\d)?$", lambda v: 90 <= float(v) <= 180, "{:.1f}"),
        2: ("COST INDEX", "ci", r"^\d{1,3}$", lambda v: 0 <= int(v) <= 500, "{}"),
        3: ("CRZ ALT", "alt", r"^(FL)?\d{3,5}$", None, "{}"),
    }

    def __init__(self) -> None:
        super().__init__()
        self.active = {"gw": "", "ci": "", "alt": ""}
        self.mod = dict(self.active)

    def draw(self, app: CduApp, pg: Page) -> None:
        for n, (label, key, _rx, _ok, _fmt) in self.fields.items():
            value = self.mod[key]
            pending = value != self.active[key]
            placeholder = "___._" if key == "gw" else "____"  # stand-in for Boeing box prompts
            # amber = required & empty, white = modified (not yet EXEC'd), cyan = active
            app.line(pg, n, value or placeholder, left_label=label,
                     color="white" if pending else ("cyan" if value else "amber"))
        app.line(pg, 4, "-8°C", left_label="ISA DEV", color="green")
        pg.fill(11, "-", "white", small=True)
        app.line(pg, 6, "<ERASE" if app.exec_pending else "<INDEX", "N1 LIMIT>")

    def on_lsk(self, app: CduApp, side: str, n: int) -> bool:
        if side == "L" and n in self.fields:
            entry = app.scratchpad
            if not entry:
                return True
            label, key, rx, ok, fmt = self.fields[n]
            entry = entry.replace(" ", "")
            if not re.match(rx, entry) or (ok and not ok(entry)):
                app.show_message("INVALID ENTRY")
                return True
            if key == "gw":
                value = fmt.format(float(entry))
            elif key == "alt":
                # 35000 -> FL350, 350 -> FL350, FL350 stays
                value = entry if entry.startswith("FL") else f"FL{int(entry) // 100:03d}" if len(entry) > 3 else f"FL{entry}"
            else:
                value = entry
            self.mod[key] = value
            app.take_scratchpad()
            app.set_exec_pending(self.mod != self.active)
            return True
        if n == 6 and side == "L":
            if app.exec_pending:
                self.mod = dict(self.active)
                app.set_exec_pending(False)
            else:
                app.go("index")
            return True
        if n == 6 and side == "R":
            app.show_message("NOT IN DEMO")
            return True
        return False

    def on_exec(self, app: CduApp) -> None:
        self.active = dict(self.mod)


class NotesPage(CduPage):
    title = "NOTES"
    page_count = 2

    def __init__(self) -> None:
        super().__init__()
        self.notes = [""] * 10

    def draw(self, app: CduApp, pg: Page) -> None:
        base = self.page_index * 5
        for i in range(5):
            idx = base + i
            app.line(pg, i + 1, self.notes[idx] or "-" * 10, left_label=f"NOTE {idx + 1}",
                     color="cyan" if self.notes[idx] else "grey")
        pg.fill(11, "-", "white", small=True)
        app.line(pg, 6, "<INDEX", "CLEAR ALL>")

    def on_lsk(self, app: CduApp, side: str, n: int) -> bool:
        if n == 6:
            if side == "L":
                app.go("index")
            else:
                self.notes = [""] * 10
            return True
        idx = self.page_index * 5 + n - 1
        if app.message == "DELETE":
            self.notes[idx] = ""
            app.message = ""
        elif app.scratchpad:
            self.notes[idx] = app.take_scratchpad()
        elif self.notes[idx]:
            app.scratchpad = self.notes[idx]  # copy a note back down to the scratchpad
        return True


class MenuPage(CduPage):
    title = "MENU"

    def draw(self, app: CduApp, pg: Page) -> None:
        app.line(pg, 1, "<FMC", "<ACT>", right_color="green")
        app.line(pg, 2, "<NOTES")
        app.line(pg, 3, "<KEY TEST")
        pg.center(9, "PYTHON SIMULATOR", "grey", small=True)
        pg.center(10, "MOZA_FCD " + __version__, "grey", small=True)

    def on_lsk(self, app: CduApp, side: str, n: int) -> bool:
        if side == "L" and n in (1, 2, 3):
            app.go({1: "index", 2: "notes", 3: "keytest"}[n])
            return True
        return False


class KeyTestPage(CduPage):
    title = "KEY TEST"

    def __init__(self) -> None:
        super().__init__()
        self.last = []

    def draw(self, app: CduApp, pg: Page) -> None:
        pg.center(2, "PRESS KEYS - MENU EXITS", "grey", small=True)
        for i, k in enumerate(self.last[-8:]):
            pg.center(4 + i, k, "green")

    def on_key(self, app: CduApp, name: str) -> bool:
        if name == "MENU":
            return False  # let the function key take us out
        self.last.append(name)
        return True


def build(screen) -> CduApp:
    app = CduApp(screen, home="index", function_keys={"INIT_REF": "index", "MENU": "menu", "N1_LIMIT": "perf"})
    app.add("index", IndexPage()).add("ident", IdentPage()).add("pos", PosPage()).add("perf", PerfPage())
    app.add("notes", NotesPage()).add("menu", MenuPage()).add("keytest", KeyTestPage())
    return app


def main(screen) -> None:
    build(screen).run()
