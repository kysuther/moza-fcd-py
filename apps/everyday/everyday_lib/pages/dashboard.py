"""Dashboard: one-glance overview. LSKs jump to the full pages."""

import time

from . import EPage
from ..services.weather import condition
from ..util import clean, mmss, rate


class DashboardPage(EPage):
    title = "DASHBOARD"

    def draw(self, app, pg):
        ctx = app.ctx
        now = time.localtime()
        app.line(pg, 1, time.strftime("%H:%M:%S", now), time.strftime("%a %d %b", now).upper(),
                 left_label="LOCAL TIME", right_label="DATE", color="green")

        f = ctx.weather.snapshot.get("forecast") if ctx.weather else None
        wx = f"{f['temp']:.0f}°{f['temp_unit']} {condition(f['code'])}" if f and f.get("temp") is not None else "--"
        app.line(pg, 2, time.strftime("%H:%M:%SZ", time.gmtime()), clean(wx, 14), left_label="UTC",
                 right_label="WEATHER", left_color="green", right_color="cyan" if f else "grey")

        m = ctx.media.snapshot
        if m.get("active") and m.get("title"):
            status = clean(m.get("status"), 7)
            artist = clean(m.get("artist"), 23 - len(status) - 3)
            label = f"{status} - {artist}" if artist else status
            app.line(pg, 3, clean(m["title"]), left_label=label, color="cyan" if m.get("status") == "Playing" else "white")
        else:
            app.line(pg, 3, "NOTHING PLAYING", left_label="MEDIA", color="grey")

        s = ctx.system.snapshot
        cpu = "--" if s.get("cpu") is None else f"{s['cpu']:.0f}"
        ram = "--" if s.get("ram") is None else f"{s['ram']:.0f}"
        gpu = s.get("gpu")
        gpu_txt = f"GPU {gpu['util']:.0f} {gpu['temp']:.0f}°" if gpu else "GPU --"
        app.line(pg, 4, f"CPU {cpu} RAM {ram}", gpu_txt, left_label="SYSTEM %", color="green")

        t = ctx.timers
        if t.countdown.running or t.countdown.is_set:
            timer_txt, timer_col = mmss(t.countdown.remaining()), "green" if t.countdown.running else "white"
        elif t.pomodoro.running:
            timer_txt, timer_col = f"{t.pomodoro.phase[0]} {mmss(t.pomodoro.timer.remaining())}", "green"
        else:
            timer_txt, timer_col = "--:--", "grey"
        app.line(pg, 5, f"DN {rate(s.get('net_down'))} UP {rate(s.get('net_up'))}", timer_txt,
                 left_label="NETWORK", right_label="TIMER", left_color="green", right_color=timer_col)

        app.line(pg, 6, "<MENU", "CLOCK>")
        self.footer_alert(app, pg)

    def on_lsk(self, app, side, n):
        target = {("L", 2): "clock", ("R", 2): "weather", ("L", 3): "media", ("L", 4): "system",
                  ("R", 4): "system", ("R", 5): "clock", ("L", 5): "system", ("L", 6): "menu", ("R", 6): "clock",
                  ("L", 1): "clock", ("R", 1): "clock"}.get((side, n))
        if target:
            app.go(target)
            return True
        return False
