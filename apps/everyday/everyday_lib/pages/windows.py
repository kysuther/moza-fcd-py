"""Windows controls (page 1) and power plans (page 2). Sleep, restart and shut down need EXEC;
restart and shut down are scheduled with a delay (config windows.shutdown_delay_s) and can be aborted."""

import time

from . import EPage
from .. import winapi
from ..util import clean, mmss

CONFIRM = {"sleep": "EXEC TO SLEEP PC", "restart": "EXEC TO RESTART PC", "shutdown": "EXEC TO SHUT DOWN PC"}
SNIP = winapi.parse_steps("WIN+SHIFT+S")
SHOW_DESKTOP = winapi.parse_steps("WIN+D")


class WindowsPage(EPage):
    title = "WINDOWS"
    page_count = 2

    def __init__(self, wall_clock=time.time):
        super().__init__()
        self.confirm = None
        self.wall_clock = wall_clock

    def on_enter(self, app):
        self.confirm = None

    def draw(self, app, pg):
        w = app.ctx.windows
        s = w.snapshot
        if not w.available:
            pg.center(6, "WINDOWS CONTROLS N/A", "amber")
        elif self.page_index == 0:
            awake = bool(s.get("keep_awake"))
            dark = s.get("dark")
            app.line(pg, 1, "<LOCK", "MONITORS OFF>")
            app.line(pg, 2, "<SLEEP", "KEEP AWAKE>", right_label="ON" if awake else "OFF",
                     label_color="green" if awake else "grey")
            app.line(pg, 3, "<RESTART", "SHUT DOWN>")
            app.line(pg, 4, "<SHOW DESKTOP", "SNIP>")
            theme = "--" if dark is None else ("DARK" if dark else "LIGHT")
            app.line(pg, 5, "<LIGHT MODE" if dark else "<DARK MODE", left_label=f"THEME {theme}",
                     left_color="grey" if dark is None else "white", label_color="grey")
            if s.get("pending"):
                pg.right(10, "ABORT>", "amber")
        else:
            plans = s.get("plans", [])[:5]
            if not plans:
                pg.center(6, "NO POWER PLANS FOUND", "grey")
            for i, p in enumerate(plans, start=1):
                app.line(pg, i, f"<{clean(p['name'], 22)}", left_label="ACTIVE" if p["active"] else "",
                         left_color="green" if p["active"] else "white", label_color="green")
        pending = s.get("pending") if w.available else None
        if self.confirm and app.exec_pending:
            pg.center(11, CONFIRM[self.confirm], "amber", small=True)
            app.line(pg, 6, "<CANCEL")
        else:
            app.line(pg, 6, "<MENU", "POWER PLANS>" if self.page_index == 0 else "CONTROLS>")
            if pending:
                left = max(0, pending["at"] - self.wall_clock())
                pg.center(11, f"{pending['kind'].upper()} IN {mmss(left)}", "amber", small=True)
            else:
                self.draw_status(app, pg)

    def on_lsk(self, app, side, n):
        w = app.ctx.windows
        if (side, n) == ("L", 6):
            if app.exec_pending:
                self.confirm = None
                app.set_exec_pending(False)
            else:
                app.go("menu")
            return True
        if (side, n) == ("R", 6):
            self.page_index = 1 - self.page_index
            return True
        if not w.available:
            return False
        if self.page_index == 1:
            plans = w.snapshot.get("plans", [])[:5]
            if side == "L" and 1 <= n <= len(plans):
                w.set_power_plan(plans[n - 1]["guid"])
                self.set_status(app, f"POWER PLAN {plans[n - 1]['name']}")
                return True
            return False
        action = {("L", 1): "lock", ("R", 1): "monitors", ("L", 2): "sleep", ("R", 2): "awake",
                  ("L", 3): "restart", ("R", 3): "shutdown", ("L", 4): "desktop", ("R", 4): "snip",
                  ("L", 5): "theme", ("R", 5): "abort"}.get((side, n))
        if action in CONFIRM:
            self.confirm = action
            app.set_exec_pending(True)
        elif action == "lock":
            w.lock()
        elif action == "monitors":
            w.monitors_off()
            self.set_status(app, "MONITORS OFF - MOVE MOUSE")
        elif action == "awake":
            w.toggle_keep_awake()
            self.set_status(app, "KEEP AWAKE OFF" if w.snapshot.get("keep_awake") else "KEEP AWAKE ON")
        elif action == "desktop":
            w.send_keys(SHOW_DESKTOP)
        elif action == "snip":
            w.send_keys(SNIP)
        elif action == "theme":
            w.toggle_dark_mode()
        elif action == "abort" and w.snapshot.get("pending"):
            w.abort_shutdown()
            self.set_status(app, "SHUTDOWN ABORTED")
        else:
            return False
        return True

    def on_exec(self, app):
        w = app.ctx.windows
        action, self.confirm = self.confirm, None
        if action == "sleep":
            w.sleep()
        elif action in ("restart", "shutdown"):
            w.shutdown(action, int(app.ctx.config["windows"]["shutdown_delay_s"]))
