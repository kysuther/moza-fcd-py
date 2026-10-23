"""Weather: now, 4-day forecast, METAR and TAF. On the METAR page, type an ICAO code + LSK 1L to look at
another airport (LSK 1L alone goes back to the home airport). R6 steps through the pages."""

import re
import time
from datetime import date

from . import EPage
from ..services.weather import compass, condition, zulu
from ..util import clean, wrap_lines

PAGES = ("NOW", "FORECAST", "METAR", "TAF")
CATEGORY_COLORS = {"VFR": "green", "MVFR": "cyan", "IFR": "red", "LIFR": "magenta"}


def _t(v) -> str:
    return "--" if v is None else f"{v:.0f}"


def age_text(then, now) -> str:
    if not then:
        return "NO DATA"
    mins = int((now - then) // 60)
    return "JUST NOW" if mins < 1 else f"{mins} MIN AGO" if mins < 120 else f"{mins // 60} HR AGO"


def taf_lines(raw: str, width: int = 24, max_lines: int = 10):
    """One line per forecast group (FM, TEMPO, BECMG, PROB), wrapped."""
    groups = re.split(r"\s+(?=(?:FM\d{6}|TEMPO|BECMG|PROB\d{2}))", clean(raw, 2000))
    lines = []
    for g in groups:
        parts = wrap_lines(g, width - 1, max_lines)       # continuation lines are indented one space
        lines += parts[:1] + [" " + p for p in parts[1:]]
    return lines[:max_lines]


class WeatherPage(EPage):
    page_count = len(PAGES)

    def __init__(self, wall_clock=time.time):
        super().__init__()
        self.wall_clock = wall_clock

    @property
    def title(self):
        return "WEATHER" if self.page_index < 2 else PAGES[self.page_index]

    def draw(self, app, pg):
        w = app.ctx.weather
        s = w.snapshot
        cfg = app.ctx.config["weather"]
        draw = (self._now, self._forecast, self._metar, self._taf)[self.page_index]
        draw(app, pg, s, cfg)
        nxt = PAGES[(self.page_index + 1) % len(PAGES)]
        app.line(pg, 6, "<MENU", f"{nxt}>")
        if s.get("error") and self.page_index < 2 and s.get("forecast"):
            pg.center(11, f"OFFLINE - {age_text(s.get('forecast_at'), self.wall_clock())}", "amber", small=True)
        elif s.get("error") and self.page_index >= 2 and s.get("metar"):
            pg.center(11, f"OFFLINE - {age_text(s.get('metar_at'), self.wall_clock())}", "amber", small=True)
        else:
            self.draw_status(app, pg)

    def _now(self, app, pg, s, cfg):
        f = s.get("forecast")
        if not f:
            pg.center(6, "LOADING WEATHER..." if not s.get("error") else "WEATHER UNAVAILABLE", "grey")
            return
        u = f["temp_unit"]
        app.line(pg, 1, f"{_t(f['temp'])}°{u}", condition(f["code"]), left_label=clean(cfg["city"], 12),
                 right_label=f"AT {f['time']}", left_color="green", right_color="cyan", label_color="grey")
        app.line(pg, 2, f"{_t(f['feels'])}°{u}", f"{_t(f['humidity'])}%", left_label="FEELS LIKE",
                 right_label="HUMIDITY", label_color="grey")
        gust = f" G{_t(f['gust'])}" if f.get("gust") and f.get("wind") and f["gust"] >= f["wind"] + 5 else ""
        today = f["days"][0] if f["days"] else None
        app.line(pg, 3, f"{compass(f['wind_dir'])} {_t(f['wind'])}{gust} {f['wind_unit']}",
                 f"{_t(today['hi'])}/{_t(today['lo'])}°" if today else "--", left_label="WIND",
                 right_label="HIGH/LOW", label_color="grey")
        if today:
            sun_label, sun = ("SUNSET", today["sunset"]) if time.strftime("%H:%M") < today["sunset"] \
                else ("SUNRISE TMRW", f["days"][1]["sunrise"] if len(f["days"]) > 1 else "--:--")
            app.line(pg, 4, f"{_t(today['rain'])}%", sun, left_label="RAIN CHANCE", right_label=sun_label,
                     label_color="grey")
        hours = f["hours"][1:7]
        if hours:
            pg.write(9, 1, "NEXT HOURS", "grey", small=True)
            pg.write(10, 0, " ".join(f"{_t(h['temp'])}" for h in hours)[:24], "white")

    def _forecast(self, app, pg, s, cfg):
        f = s.get("forecast")
        if not f:
            pg.center(6, "NO FORECAST YET", "grey")
            return
        for n, d in enumerate(f["days"][:5], start=1):
            name = "TODAY" if n == 1 else date.fromisoformat(d["date"]).strftime("%a").upper()
            app.line(pg, n, condition(d["code"]), f"{_t(d['hi'])}/{_t(d['lo'])}°", left_label=name,
                     right_label=f"RAIN {_t(d['rain'])}%", label_color="grey",
                     left_color="cyan", right_color="white")

    def _metar(self, app, pg, s, cfg):
        m = s.get("metar")
        station = s.get("airport") or cfg["metar_airport"]
        if not m:
            app.line(pg, 1, f"<{station}", left_label="AIRPORT", label_color="grey")
            pg.center(6, "NO METAR" if s.get("metar_at") else "LOADING METAR...", "grey")
            return
        cat = m["category"].upper()
        app.line(pg, 1, f"<{m['station']}", cat or "--", left_label="AIRPORT",
                 right_label=age_text(m["obs_time"], self.wall_clock()), label_color="grey",
                 left_color="white", right_color=CATEGORY_COLORS.get(cat, "white"))
        if not m.get("wspd"):
            wind = "CALM"
        else:
            wdir = "VRB" if m.get("wdir") in (None, "VRB") else f"{int(m['wdir']):03d}"
            wind = f"{wdir}/{m['wspd']}" + (f"G{m['wgst']}" if m.get("wgst") else "") + "KT"
        app.line(pg, 2, wind, f"{m['visib']} SM" if m.get("visib") is not None else "--", left_label="WIND",
                 right_label="VISIBILITY", label_color="grey")
        temp = "--" if m.get("temp") is None else f"{_t(m['temp'])}/{_t(m['dew'])}°C"
        app.line(pg, 3, clean(m["clouds"] or "--", 13), temp, left_label="CLOUDS", right_label="TEMP/DEW",
                 label_color="grey")
        app.line(pg, 4, f"{m['altim_inhg']:.2f} IN" if m.get("altim_inhg") else "--",
                 f"{m['altim_hpa']:.0f} HPA" if m.get("altim_hpa") else "--", left_label="ALTIMETER",
                 right_label=zulu(m["obs_time"]), label_color="grey")
        for r, text in enumerate(wrap_lines(m["raw"], 24, 2), start=9):
            pg.write(r, 0, text, "white", small=True)

    def _taf(self, app, pg, s, cfg):
        t = s.get("taf")
        if not t or not t.get("raw"):
            pg.center(6, "NO TAF", "grey")
            return
        for r, text in enumerate(taf_lines(t["raw"]), start=1):
            pg.write(r, 0, text, "white", small=True)

    def on_lsk(self, app, side, n):
        w = app.ctx.weather
        if (side, n) == ("L", 6):
            app.go("menu")
        elif (side, n) == ("R", 6):
            self.page_index = (self.page_index + 1) % len(PAGES)
        elif self.page_index == 2 and (side, n) == ("L", 1):
            entry = app.scratchpad.strip().upper()
            if entry and not re.fullmatch(r"[A-Z0-9]{3,4}", entry):
                app.show_message("ENTER ICAO CODE")
                return True
            if entry:
                app.take_scratchpad()
            w.set_airport(entry or None)
            self.set_status(app, f"LOADING {entry or app.ctx.config['weather']['metar_airport']}")
        elif self.page_index < 2 and (side, n) == ("L", 1):
            w.refresh()
            self.set_status(app, "REFRESHING")
        else:
            return False
        return True
