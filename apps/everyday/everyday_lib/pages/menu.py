"""Home menu. Pages from later phases are listed in grey and say so when selected."""

from . import EPage

# (side, line) -> (label, page name or None if not built yet, phase)
ENTRIES = {
    ("L", 1): ("DASHBOARD", "dashboard"), ("R", 1): ("LAUNCHER", "launcher"),
    ("L", 2): ("AUDIO", "audio"),         ("R", 2): ("TOOLS", "tools"),
    ("L", 3): ("MEDIA", "media"),         ("R", 3): ("DISCORD", "discord"),
    ("L", 4): ("SYSTEM", "system"),       ("R", 4): ("WEATHER", "weather"),
    ("L", 5): ("CLOCK/TIMERS", "clock"),  ("R", 5): ("SIMBRIEF", "simbrief"),
    ("L", 6): ("WINDOWS", "windows"),     ("R", 6): ("SETTINGS", "settings"),
}


class MenuPage(EPage):
    title = "MENU"

    def draw(self, app, pg):
        for n in range(1, 7):
            left, lpage = ENTRIES[("L", n)]
            right, rpage = ENTRIES[("R", n)]
            app.line(pg, n, f"<{left}", f"{right}>", left_color="white" if lpage else "grey",
                     right_color="white" if rpage else "grey")
        self.footer_alert(app, pg)

    def on_lsk(self, app, side, n):
        label, page = ENTRIES[(side, n)]
        if page:
            app.go(page)
        else:
            app.show_message("COMING IN A LATER PHASE")
        return True
