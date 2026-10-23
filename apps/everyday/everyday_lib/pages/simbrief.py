"""SimBrief: your latest flight plan. Pages: summary, fuel, weights, route, departure/arrival weather.
NEXT/PREV PAGE step through them; REFRESH> fetches the plan again."""

import time
from datetime import datetime, timezone

from . import EPage
from ..util import clean, wrap_lines

TITLES = ("SIMBRIEF", "FUEL", "WEIGHTS", "ROUTE", "DEP/ARR WX")


def hm(seconds) -> str:
    if seconds is None:
        return "--"
    m = int(seconds) // 60
    return f"{m // 60}:{m % 60:02d}"


def zulu(epoch) -> str:
    return "--" if not epoch else datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%H%MZ")


def num(v) -> str:
    return "--" if v is None else f"{v:,}"


def ago(then, now) -> str:
    mins = int((now - then) // 60)
    return f"{mins} MIN AGO" if mins < 120 else f"{mins // 60} HR AGO" if mins < 2880 else f"{mins // 1440} DAYS AGO"


class SimBriefPage(EPage):
    page_count = len(TITLES)

    def __init__(self, wall_clock=time.time):
        super().__init__()
        self.wall_clock = wall_clock

    @property
    def title(self):
        return TITLES[self.page_index]

    def draw(self, app, pg):
        sb = app.ctx.simbrief
        s = sb.snapshot
        plan = s.get("plan")
        if not s.get("configured"):
            pg.center(5, "NOT SET UP", "amber")
            pg.center(7, "ADD [SIMBRIEF] USERID", "grey", small=True)
            pg.center(8, "IN CONFIG.TOML", "grey", small=True)
        elif not plan:
            pg.center(5, "LOADING PLAN..." if s.get("fetching") or not s.get("error") else "NO PLAN", "grey")
            if s.get("error"):
                for r, text in enumerate(wrap_lines(s["error"], 24, 2), start=7):
                    pg.center(r, text, "amber", small=True)
        else:
            (self._summary, self._fuel, self._weights, self._route, self._wx)[self.page_index](app, pg, plan)
        app.line(pg, 6, "<MENU", "REFRESH>")
        if s.get("fetching"):
            pg.center(11, "FETCHING PLAN...", "grey", small=True)
        elif plan and s.get("error"):
            pg.center(11, "OFFLINE - SHOWING LAST PLAN", "amber", small=True)
        elif plan and self.page_index == 0 and plan.get("generated"):
            pg.center(11, f"{ago(plan['generated'], self.wall_clock())} - AIRAC {plan['airac']}", "grey", small=True)
        else:
            self.draw_status(app, pg)

    def _summary(self, app, pg, p):
        o, d, a = p["origin"], p["destination"], p["alternate"]
        app.line(pg, 1, o["icao"] or "--", d["icao"] or "--", left_label=f"ORIGIN {o['rwy']}".strip(),
                 right_label=f"DEST {d['rwy']}".strip(), color="green", label_color="grey")
        app.line(pg, 2, clean(p["callsign"], 10), clean(p["aircraft"], 6), left_label="CALLSIGN",
                 right_label=clean(p["reg"], 10), color="cyan", label_color="grey")
        alt = p.get("initial_alt")
        app.line(pg, 3, f"FL{alt // 100:03d}" if alt else "--", p["cost_index"] or "--", left_label="INITIAL ALT",
                 right_label="COST INDEX", label_color="grey")
        app.line(pg, 4, zulu(p["sched_out"]), zulu(p["sched_in"]), left_label="BLOCK OUT", right_label="BLOCK IN",
                 label_color="grey")
        app.line(pg, 5, f"{p['distance'] or '--'} NM {hm(p['ete'])}", a["icao"] or "NONE",
                 left_label="DISTANCE / ETE", right_label="ALTN", label_color="grey")

    def _fuel(self, app, pg, p):
        f, u = p["fuel"], p["units"]
        rows = ((("BLOCK", "plan_ramp"), ("TAKEOFF", "plan_takeoff")), (("TRIP", "enroute_burn"), ("TAXI", "taxi")),
                (("CONTINGENCY", "contingency"), ("ALTERNATE", "alternate_burn")),
                (("RESERVE", "reserve"), ("EXTRA", "extra")), (("LANDING", "plan_landing"), (None, None)))
        for n, ((ll, lk), (rl, rk)) in enumerate(rows, start=1):
            app.line(pg, n, num(f[lk]), num(f[rk]) if rk else u, left_label=ll, right_label=rl or "UNITS",
                     left_color="green" if n == 1 else "white", label_color="grey")

    def _weights(self, app, pg, p):
        w, u = p["weights"], p["units"]
        for n, (label, est, mx) in enumerate((("ZFW", "est_zfw", "max_zfw"), ("TOW", "est_tow", "max_tow"),
                                              ("LDW", "est_ldw", "max_ldw")), start=1):
            over = w[est] is not None and w[mx] is not None and w[est] > w[mx]
            app.line(pg, n, num(w[est]), num(w[mx]), left_label=f"EST {label} {u}", right_label=f"MAX {label}",
                     left_color="amber" if over else "green", label_color="grey")
        app.line(pg, 4, num(w["payload"]), str(w["pax_count"] if w["pax_count"] is not None else "--"),
                 left_label="PAYLOAD", right_label="PASSENGERS", label_color="grey")
        app.line(pg, 5, num(w["cargo"]), num(w["oew"]), left_label="CARGO", right_label="OEW", label_color="grey")

    def _route(self, app, pg, p):
        o, d = p["origin"], p["destination"]
        text = f"{o['icao']}/{o['rwy']} {p['route']} {d['icao']}/{d['rwy']}"
        lines = wrap_lines(text, 24, 10)
        for r, line in enumerate(lines, start=1):
            pg.write(r, 0, line, "green" if r % 2 else "white", small=bool(r % 2))

    def _wx(self, app, pg, p):
        row = 1
        for label, apt in (("DEP", p["origin"]), ("ARR", p["destination"])):
            pg.write(row, 1, f"{label} {apt['icao']} - AT PLANNING", "grey", small=True)
            for r, text in enumerate(wrap_lines(apt["metar"] or "NO METAR", 24, 4), start=row + 1):
                pg.write(r, 0, text, "white", small=True)
            row += 5

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            app.go("menu")
        elif (side, n) == ("R", 6):
            app.ctx.simbrief.refresh()
            self.set_status(app, "REFRESHING")
        else:
            return False
        return True
