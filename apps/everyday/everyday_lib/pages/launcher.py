"""Launcher: up to 10 items per page from config.toml [launcher] items (left column first, then right).
Items with confirm = true wait for EXEC."""

from . import EPage

PER_PAGE = 10


class LauncherPage(EPage):
    title = "LAUNCHER"

    def __init__(self, items):
        super().__init__()
        self.items = list(items)
        self.page_count = max(1, -(-len(self.items) // PER_PAGE))
        self.confirm = None

    def on_enter(self, app):
        self.confirm = None

    def item_at(self, side, n):
        i = self.page_index * PER_PAGE + (n - 1) + (0 if side == "L" else 5)
        return self.items[i] if 1 <= n <= 5 and i < len(self.items) else None

    def draw(self, app, pg):
        if not self.items:
            pg.center(5, "NO ITEMS YET", "grey")
            pg.center(7, "ADD THEM IN CONFIG.TOML", "grey", small=True)
        for n in range(1, 6):
            left, right = self.item_at("L", n), self.item_at("R", n)
            app.line(pg, n, f"<{left.label}" if left else "", f"{right.label}>" if right else "",
                     left_label=left.kind_label if left else "", right_label=right.kind_label if right else "",
                     left_color="grey" if left and left.error else "white",
                     right_color="grey" if right and right.error else "white", label_color="grey")
        if self.confirm and app.exec_pending:
            pg.center(11, f"EXEC TO RUN {self.confirm.label}", "amber", small=True)
            app.line(pg, 6, "<CANCEL")
        else:
            app.line(pg, 6, "<MENU")
            failed = app.ctx.windows.snapshot.get("failed")
            if failed:
                pg.center(11, failed, "amber", small=True)
            else:
                self.draw_status(app, pg)

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            if app.exec_pending:
                self.confirm = None
                app.set_exec_pending(False)
            else:
                app.go("menu")
            return True
        item = self.item_at(side, n)
        if item is None:
            return False
        if item.error:
            app.show_message(f"{item.label} {item.error}")
        elif item.confirm:
            self.confirm = item
            app.set_exec_pending(True)
        else:
            self.run(app, item)
        return True

    def on_exec(self, app):
        item, self.confirm = self.confirm, None
        if item:
            self.run(app, item)

    def run(self, app, item):
        w = app.ctx.windows
        if not w.available:
            app.show_message("WINDOWS CONTROLS N/A")
        elif item.kind == "run":
            w.launch(item.run, item.args)
            self.set_status(app, f"OPENING {item.label}")
        elif item.kind == "keys":
            w.send_keys(item.steps)
            self.set_status(app, f"SENT {item.label}")
        elif item.kind == "text":
            w.type_text(item.text)
            self.set_status(app, f"TYPED {item.label}")
