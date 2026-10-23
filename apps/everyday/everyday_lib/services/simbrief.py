"""Latest SimBrief flight plan (OFP) for one pilot, via SimBrief's public fetcher API.

Fetched at startup, every 30 minutes, and on refresh(). Uses the numeric user ID when set (it never
changes), otherwise the username."""

from __future__ import annotations

import time
from typing import Optional

from . import Worker
from .. import net

FETCH_URL = "https://www.simbrief.com/api/xml.fetcher.php"
EVERY_S = 30 * 60
RETRY_S = 120


def _s(v) -> str:
    """SimBrief's JSON uses {} for empty values."""
    return "" if v is None or isinstance(v, (dict, list)) else str(v)


def _i(v) -> Optional[int]:
    try:
        return int(float(_s(v)))
    except ValueError:
        return None


def _airport(a: dict) -> dict:
    a = a or {}
    return {"icao": _s(a.get("icao_code")), "name": _s(a.get("name")), "rwy": _s(a.get("plan_rwy")),
            "metar": _s(a.get("metar")), "taf": _s(a.get("taf"))}


def parse_ofp(d: dict) -> dict:
    """The OFP fields the SimBrief pages show. Raises ValueError if SimBrief reports no plan."""
    status = _s((d.get("fetch") or {}).get("status"))
    if status != "Success":
        raise ValueError(status or "no flight plan")
    g, f, w, t = d.get("general") or {}, d.get("fuel") or {}, d.get("weights") or {}, d.get("times") or {}
    ac, p, atc = d.get("aircraft") or {}, d.get("params") or {}, d.get("atc") or {}
    altn = d.get("alternate") or {}
    if isinstance(altn, list):          # several alternates: the first is the planned one
        altn = altn[0] if altn else {}
    return {
        "callsign": _s(atc.get("callsign")) or _s(g.get("icao_airline")) + _s(g.get("flight_number")),
        "origin": _airport(d.get("origin")), "destination": _airport(d.get("destination")),
        "alternate": _airport(altn),
        "aircraft": _s(ac.get("icaocode")), "aircraft_name": _s(ac.get("name")), "reg": _s(ac.get("reg")),
        "route": _s(g.get("route")), "initial_alt": _i(g.get("initial_altitude")),
        "cost_index": _s(g.get("costindex")), "distance": _i(g.get("route_distance")),
        "passengers": _i(g.get("passengers")),
        "ete": _i(t.get("est_time_enroute")), "sched_out": _i(t.get("sched_out")), "sched_in": _i(t.get("sched_in")),
        "sched_off": _i(t.get("sched_off")), "sched_on": _i(t.get("sched_on")),
        "units": (_s(p.get("units")) or "lbs").upper(), "airac": _s(p.get("airac")),
        "generated": _i(p.get("time_generated")),
        "fuel": {k: _i(f.get(k)) for k in ("plan_ramp", "plan_takeoff", "enroute_burn", "taxi", "contingency",
                                           "alternate_burn", "reserve", "extra", "plan_landing")},
        "weights": {k: _i(w.get(k)) for k in ("est_zfw", "max_zfw", "est_tow", "max_tow", "est_ldw", "max_ldw",
                                              "payload", "pax_count", "cargo", "oew")},
    }


class SimBriefService(Worker):
    name = "simbrief"
    interval = 5.0

    def __init__(self, userid: str = "", username: str = "", fetch=net.get_json, wall_clock=time.time) -> None:
        super().__init__()
        self.userid, self.username = str(userid or "").strip(), str(username or "").strip()
        self.fetch = fetch
        self.wall_clock = wall_clock
        self._next = 0.0
        self.snapshot = {"plan": None, "fetched_at": None, "error": "", "fetching": False,
                         "configured": bool(self.userid or self.username)}

    def poll(self) -> None:
        if not self.snapshot["configured"] or self.wall_clock() < self._next:
            return
        self.snapshot = dict(self.snapshot, fetching=True)
        snap = dict(self.snapshot, fetching=False)
        params = {"json": 1, **({"userid": self.userid} if self.userid else {"username": self.username})}
        try:
            snap["plan"] = parse_ofp(self.fetch(FETCH_URL, params, timeout=20))
            snap["fetched_at"] = self.wall_clock()
            snap["error"] = ""
            self._next = self.wall_clock() + EVERY_S
        except ValueError as e:                # SimBrief answered, but there's no plan
            snap["error"] = str(e).upper()
            self._next = self.wall_clock() + EVERY_S
        except Exception as e:
            snap["error"] = f"{type(e).__name__}: {e}"
            self._next = self.wall_clock() + RETRY_S
        self.snapshot = snap

    def refresh(self) -> None:
        self.do(self._refresh)

    def _refresh(self) -> None:
        self._next = 0.0
