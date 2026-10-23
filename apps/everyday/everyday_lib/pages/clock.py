"""Clock (local + world clocks) and timers (countdown, stopwatch, Pomodoro) - 2 pages, PREV/NEXT switches."""

import datetime
import time

from . import EPage
from ..util import clean, mmss, parse_duration

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


def _zone_time(zone: str):
    if ZoneInfo is None:
        return None
    try:
        return datetime.datetime.now(ZoneInfo(zone))
    except Exception:
        return None


class ClockPage(EPage):
    title = "CLOCK"
    page_count = 2

    def draw(self, app, pg):
        if self.page_index == 0:
            self._draw_clocks(app, pg)
            app.line(pg, 6, "<MENU", "TIMERS>")
        else:
            self._draw_timers(app, pg)
            app.line(pg, 6, "<MENU", "SKIP POMO>", right_label="NEXT PG: CLOCKS")
        self.footer_alert(app, pg)

    def _draw_clocks(self, app, pg):
        now = datetime.datetime.now().astimezone()
        app.line(pg, 1, now.strftime("%H:%M:%S"), now.strftime("%a %d %b").upper(), left_label="LOCAL",
                 right_label=clean(now.strftime("%Z"), 12), color="green")
        today = now.date()
        for i, entry in enumerate(app.ctx.config["clock"]["world"][:4], start=2):
            zone = entry.get("zone", "UTC")
            label = clean(entry.get("label") or zone.split("/")[-1].replace("_", " "), 10)
            t = _zone_time(zone)
            if t is None:
                app.line(pg, i, "BAD ZONE", left_label=label, color="amber")
                continue
            diff = (t.date() - today).days
            day = t.strftime("%a").upper() + ("" if diff == 0 else f" {diff:+d}")
            app.line(pg, i, t.strftime("%H:%M"), day, left_label=label, color="cyan")

    def _draw_timers(self, app, pg):
        t = app.ctx.timers
        cd = t.countdown
        if cd.running:
            cd_col, cd_btn = "green", "PAUSE>"
        elif cd.is_set and cd.remaining() > 0:
            cd_col, cd_btn = "white", "START>" if cd.remaining() == cd.duration else "RESUME>"
        else:
            cd_col, cd_btn = "amber", "START>"
        cd_txt = mmss(cd.remaining()) if cd.is_set else "__:__"
        app.line(pg, 1, cd_txt, cd_btn, left_label="TIMER (TYPE MIN, LSK)", left_color=cd_col)
        app.line(pg, 2, "<RESET")
        sw = t.stopwatch
        el = sw.elapsed()
        app.line(pg, 3, f"{mmss(el)}.{int(el * 10) % 10}", "STOP>" if sw.running else "START>", left_label="STOPWATCH",
                 left_color="green" if sw.running else "white")
        app.line(pg, 4, "<RESET")
        p = t.pomodoro
        app.line(pg, 5, mmss(p.timer.remaining()), "PAUSE>" if p.running else "START>",
                 left_label=f"POMODORO {p.phase} #{p.count + 1}",
                 left_color="green" if p.running else "white", label_color="green" if p.phase == "FOCUS" else "cyan")

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            app.go("menu")
            return True
        if (side, n) == ("R", 6):
            if self.page_index == 0:
                self.page_index = 1
            else:
                app.ctx.timers.pomodoro.skip()
            return True
        if self.page_index != 1:
            return False
        t = app.ctx.timers
        if (side, n) == ("L", 1):
            if not app.scratchpad:
                app.show_message("TYPE MIN OR M:SS, LSK")
            else:
                secs = parse_duration(app.scratchpad)
                if secs is None:
                    app.show_message("INVALID ENTRY")
                else:
                    app.take_scratchpad()
                    t.countdown.set(secs)
        elif (side, n) == ("R", 1):
            if t.countdown.running:
                t.countdown.pause()
            elif t.countdown.is_set and t.countdown.remaining() > 0:
                t.countdown.start()
            else:
                app.show_message("SET A TIME FIRST")
        elif (side, n) == ("L", 2):
            t.countdown.reset()
        elif (side, n) == ("R", 3):
            t.stopwatch.toggle()
        elif (side, n) == ("L", 4):
            t.stopwatch.reset()
        elif (side, n) == ("R", 5):
            t.pomodoro.toggle()
        else:
            return False
        return True
