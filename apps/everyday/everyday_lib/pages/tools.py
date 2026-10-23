"""Tools: calculator, unit converter, notes and the Windows clipboard."""

import math

from . import EPage
from ..calc import CONVERSIONS, CalcError, convert, evaluate, fmt, round_for_display
from ..notes import MAX_NOTES
from ..util import clean, wrap_lines


class ToolsPage(EPage):
    title = "TOOLS"
    ENTRIES = {("L", 1): "calculator", ("L", 2): "converter", ("L", 3): "notes", ("L", 4): "clipboard"}

    def draw(self, app, pg):
        app.line(pg, 1, "<CALCULATOR")
        app.line(pg, 2, "<CONVERTER", left_label="UNITS", label_color="grey")
        app.line(pg, 3, "<NOTES", left_label=f"{len(app.ctx.notes)} SAVED", label_color="grey")
        app.line(pg, 4, "<CLIPBOARD", left_label="TEXT + HISTORY", label_color="grey")
        self.back(app, pg)

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            app.go("menu")
        elif (side, n) in self.ENTRIES:
            app.go(self.ENTRIES[(side, n)])
        else:
            return False
        return True


class CalculatorPage(EPage):
    """Type a calculation (X = times), then CALC> or EXEC. The last 4 results show on the left; their
    LSKs put the value back in the scratchpad. Functions apply to the scratchpad value, or the answer."""

    title = "CALCULATOR"
    FUNCS = {1: "SQRT", 2: "SQUARE", 3: "1/X", 4: "PERCENT"}

    def __init__(self):
        super().__init__()
        self.history = []            # (expression, value), newest first
        self.ans = None

    def draw(self, app, pg):
        for i, (expr, value) in enumerate(self.history[:4], start=1):
            app.line(pg, i, f"<{fmt(value)}", left_label=clean(expr, 12), label_color="grey",
                     left_color="green" if i == 1 else "white")
        if not self.history:
            pg.write(3, 1, "TYPE 12+3X4, THEN CALC>", "grey", small=True)
            pg.write(5, 1, "X = TIMES, +/- KEY = - +", "grey", small=True)
            pg.write(7, 1, "+5 / X2 USE LAST ANSWER", "grey", small=True)
        for n, name in self.FUNCS.items():
            app.line(pg, n, right=f"{name}>")
        app.line(pg, 5, "<CLEAR" if self.history else "", "COPY>" if self.ans is not None else "",
                 right_label="TO PC" if self.ans is not None else "", label_color="grey")
        app.line(pg, 6, "<TOOLS", "CALC>", right_label="OR EXEC", label_color="grey")
        self.draw_status(app, pg)

    def on_key(self, app, name):
        if name == "EXEC" and not app.exec_pending:
            self.calculate(app)
            return True
        return False

    def push(self, expr, value):
        self.history.insert(0, (expr, value))
        del self.history[8:]
        self.ans = value

    def calculate(self, app):
        entry = app.scratchpad
        try:
            value = evaluate(entry, self.ans)
        except CalcError as e:
            app.show_message(str(e))
            return
        app.take_scratchpad()
        self.push(entry or fmt(value), value)

    def apply(self, app, func):
        try:
            x = evaluate(app.scratchpad, self.ans)
        except CalcError as e:
            app.show_message(str(e))
            return
        if func == "SQRT" and x < 0:
            app.show_message("NEGATIVE NUMBER")
            return
        if func == "1/X" and x == 0:
            app.show_message("DIVIDE BY ZERO")
            return
        value = {"SQRT": math.sqrt, "SQUARE": lambda v: v * v, "1/X": lambda v: 1 / v,
                 "PERCENT": lambda v: v / 100}[func](x)
        if math.isinf(value):
            app.show_message("OUT OF RANGE")
            return
        app.take_scratchpad()
        self.push(f"{func} {fmt(x)}", value)

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            app.go("tools")
        elif (side, n) == ("R", 6):
            self.calculate(app)
        elif side == "R" and n in self.FUNCS:
            self.apply(app, self.FUNCS[n])
        elif side == "L" and 1 <= n <= 4 and n <= len(self.history):
            app.message = ""
            app.scratchpad = fmt(self.history[n - 1][1])
        elif (side, n) == ("L", 5):
            self.history.clear()
            self.ans = None
        elif (side, n) == ("R", 5) and self.ans is not None:
            app.ctx.clipboard.set_text(fmt(self.ans))
            self.set_status(app, f"COPIED {fmt(self.ans)}")
        else:
            return False
        return True


class ConverterPage(EPage):
    """Type a value, then the LSK of the unit to convert FROM. With an empty scratchpad, the last result
    is converted again (so you can chain)."""

    title = "CONVERTER"
    page_count = 2

    def __init__(self):
        super().__init__()
        self.result = ""
        self.last = None

    def draw(self, app, pg):
        for n in range(1, 6):
            i = self.page_index * 5 + n - 1
            a, b = CONVERSIONS[i][0], CONVERSIONS[i][1]
            app.line(pg, n, f"<{a}", f"{b}>")
            pg.center(2 * n - 1, f"{a} <-> {b}", "grey", small=True)
        if self.result:
            pg.center(11, self.result, "green")
        else:
            pg.center(11, "VALUE + LSK OF FROM UNIT", "grey", small=True)
        app.line(pg, 6, "<TOOLS")

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            app.go("tools")
            return True
        if not 1 <= n <= 5:
            return False
        if app.scratchpad:
            try:
                value = evaluate(app.scratchpad)
            except CalcError as e:
                app.show_message(str(e))
                return True
            app.take_scratchpad()
        elif self.last is not None:
            value = self.last
        else:
            app.show_message("ENTER A VALUE")
            return True
        src, dst, out = convert(self.page_index * 5 + n - 1, side == "R", value)
        out = round_for_display(out)
        self.last = out
        self.result = clean(f"{fmt(value, 8)} {src} = {fmt(out, 8)} {dst}")
        return True


class NotesPage(EPage):
    """Notes, 5 per page. Type + LSK adds (on the empty line) or replaces; LSK alone copies the note to
    the scratchpad for editing; DEL then LSK deletes. RSK copies the note to the PC clipboard."""

    title = "NOTES"

    def on_enter(self, app):
        self._paginate(app)

    def _paginate(self, app):
        count = len(app.ctx.notes)
        self.page_count = max(1, min(count, MAX_NOTES - 1) // 5 + 1)
        self.page_index = min(self.page_index, self.page_count - 1)

    def draw(self, app, pg):
        notes = app.ctx.notes.notes
        for n in range(1, 6):
            i = self.page_index * 5 + n - 1
            if i < len(notes):
                app.line(pg, n, clean(notes[i]), left_label=f"NOTE {i + 1}", right_label="COPY>", label_color="grey")
            elif i == len(notes) and i < MAX_NOTES:
                app.line(pg, n, "-" * 10, left_label="NEW NOTE", left_color="amber", label_color="grey")
        if self.status and app.ctx.clock() < self.status_until:
            self.draw_status(app, pg)
        else:
            pg.center(11, "DEL THEN LSK DELETES", "grey", small=True)
        app.line(pg, 6, "<TOOLS")

    def on_lsk(self, app, side, n):
        store = app.ctx.notes
        if (side, n) == ("L", 6):
            app.go("tools")
            return True
        i = self.page_index * 5 + n - 1
        if not 1 <= n <= 5 or i > len(store):
            return False
        if side == "R":
            if i < len(store):
                app.ctx.clipboard.set_text(store.notes[i])
                self.set_status(app, "COPIED TO PC CLIPBOARD")
            return True
        if app.message == "DELETE":
            app.message = ""
            if i < len(store):
                store.delete(i)
        elif app.scratchpad:
            text = app.take_scratchpad()
            if i < len(store):
                store.replace(i, text)
            elif not store.add(text):
                app.show_message("NOTES FULL")
        elif i < len(store):
            app.scratchpad = store.notes[i]
        self._paginate(app)             # before the redraw, which writes the "1/2" first
        return True


class ClipboardPage(EPage):
    """Page 1: the clipboard's text, CLEAR, and SET (scratchpad -> clipboard). Page 2: recent clips;
    LSK puts one back on the clipboard. The history is kept in memory only."""

    title = "CLIPBOARD"
    page_count = 2

    def draw(self, app, pg):
        c = app.ctx.clipboard
        s = c.snapshot
        if not c.available:
            pg.center(6, "CLIPBOARD N/A", "amber")
        elif self.page_index == 0:
            lines = wrap_lines(s.get("text", ""), 24, 8)
            if lines:
                for r, text in enumerate(lines, start=1):
                    pg.write(r, 0, text, "white", small=True)
            else:
                pg.center(4, "NO TEXT ON CLIPBOARD", "grey")
            app.line(pg, 5, "<CLEAR" if lines else "", "SET>", right_label="FROM SCRATCHPAD", label_color="grey")
        else:
            history = s.get("history", [])[1:6]
            if not history:
                pg.center(6, "NO EARLIER CLIPS", "grey")
            for i, text in enumerate(history, start=1):
                app.line(pg, i, f"<{clean(text, 23)}", left_label=f"{i} BACK", label_color="grey")
        app.line(pg, 6, "<TOOLS", "HISTORY>" if self.page_index == 0 else "CURRENT>")
        self.draw_status(app, pg, row=11)

    def on_lsk(self, app, side, n):
        c = app.ctx.clipboard
        if (side, n) == ("L", 6):
            app.go("tools")
        elif (side, n) == ("R", 6):
            self.page_index = 1 - self.page_index
        elif not c.available:
            return False
        elif self.page_index == 0 and (side, n) == ("L", 5):
            c.clear()
            self.set_status(app, "CLIPBOARD CLEARED")
        elif self.page_index == 0 and (side, n) == ("R", 5):
            if not app.scratchpad:
                app.show_message("TYPE TEXT FIRST")
            else:
                c.set_text(app.take_scratchpad())
                self.set_status(app, "SET ON PC CLIPBOARD")
        elif self.page_index == 1 and side == "L" and 1 <= n <= 5:
            history = c.snapshot.get("history", [])[1:6]
            if n > len(history):
                return False
            c.set_text(history[n - 1])
            self.set_status(app, "RESTORED TO CLIPBOARD")
        else:
            return False
        return True
