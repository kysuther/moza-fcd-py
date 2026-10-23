"""Settings, 2 pages.

Page 1: device brightness (screen %, key backlight /15, annunciators /15), idle dimming (LSK 4L cycles
the delay, RSK 4 the dim level) and auto brightness (LSK 5L: the device's photoresistor switch).
Page 2: start page and light test.

Brightness and the photoresistor switch are stored on the device itself (the same values MOZA Cockpit
changes). The device can't report the switch, so the app shows the state it last set (settings.json),
like the start page and idle options - except that changing light readings prove it's ON (they freeze
while it's off), so the app corrects an OFF label by itself.
"""

import threading
import time

from . import EPage
from ..app import LEVEL_STEP, SCREEN_STEP
from ..config import IDLE_MINUTES_CHOICES, IDLE_SCREEN_CHOICES, START_PAGES

LIGHTS = ("CALL", "FAIL", "EXEC", "MSG", "OFST")
ROWS = (("screen", "SCREEN", "%"), ("keys", "KEY BACKLIGHT", "/15"), ("lamps", "ANNUNCIATORS", "/15"))


def _next(choices, current):
    """The next value in choices after current (wrapping); the first one if current isn't listed."""
    return choices[(choices.index(current) + 1) % len(choices)] if current in choices else choices[0]


class SettingsPage(EPage):
    title = "SETTINGS"
    page_count = 2

    def draw(self, app, pg):
        if self.page_index == 0:
            self._levels(app, pg)
        else:
            self._general(app, pg)
        self.back(app, pg)

    def _levels(self, app, pg):
        ctx = app.ctx
        for n, (which, name, unit) in enumerate(ROWS, start=1):
            value = ctx.levels.get(which)
            shown = "--" if value is None else f"{value}{unit}"
            app.line(pg, n, "<DIM", "BRIGHT>", left_label=f"{name} {shown}", label_color="green")
        minutes = ctx.settings["idle_minutes"]
        app.line(pg, 4, f"<{minutes} MIN" if minutes else "<NEVER", f"{ctx.settings['idle_screen']}%>",
                 left_label="IDLE DIM AFTER", right_label="DIM TO", color="cyan")
        auto = ctx.settings["auto_brightness"]
        app.line(pg, 5, "<--" if auto is None else ("<ON" if auto else "<OFF"), left_label="AUTO BRIGHTNESS",
                 left_color="grey" if auto is None else "green" if auto else "amber")
        ambient = getattr(app.screen, "light_levels", {}).get("LIGHT_SENSOR_AB", 0)
        if ambient and auto is not False:     # readings freeze while the switch is off
            pg.center(11, f"AMBIENT LIGHT {ambient * 100 // 65535}%", "green", small=True)
        else:
            pg.center(11, "TYPE VALUE + <DIM SETS", "grey", small=True)

    def _general(self, app, pg):
        app.line(pg, 1, f"<{app.ctx.settings['start_page'].upper()}", "LIGHT TEST>", left_label="START PAGE",
                 left_color="cyan")

    def on_lsk(self, app, side, n):
        ctx = app.ctx
        if (side, n) == ("L", 6):
            app.go("menu")
        elif self.page_index == 1:
            if (side, n) == ("L", 1):
                ctx.settings.set("start_page", _next(START_PAGES, ctx.settings["start_page"]))
            elif (side, n) == ("R", 1):
                threading.Thread(target=self._light_test, args=(app,), daemon=True).start()
            else:
                return False
        elif 1 <= n <= 3:
            which, _, _ = ROWS[n - 1]
            step = SCREEN_STEP if which == "screen" else LEVEL_STEP
            if side == "L" and app.scratchpad:
                entry = app.scratchpad.strip()
                limit = app.LIMITS[which]
                if entry.isdigit() and 0 <= int(entry) <= limit:
                    app.take_scratchpad()
                    app.set_level(which, int(entry))
                else:
                    app.show_message(f"ENTER 0-{limit}")
            else:
                app.step_level(which, -step if side == "L" else step)
        elif (side, n) == ("L", 4):
            ctx.settings.set("idle_minutes", _next(IDLE_MINUTES_CHOICES, ctx.settings["idle_minutes"]))
        elif (side, n) == ("R", 4):
            ctx.settings.set("idle_screen", _next(IDLE_SCREEN_CHOICES, ctx.settings["idle_screen"]))
        elif (side, n) == ("L", 5):
            on = not ctx.settings["auto_brightness"]           # unknown (None) -> ON
            if app.set_auto_brightness(on):
                app.show_message("AUTO BRIGHTNESS ON" if on else "AUTO BRIGHTNESS OFF")
            else:
                app.show_message("DEVICE NOT READY")
        else:
            return False
        return True

    @staticmethod
    def _light_test(app):
        for name in LIGHTS:
            app.screen.set_light(name, True)
            time.sleep(0.4)
        time.sleep(0.8)
        app.lights_off()
