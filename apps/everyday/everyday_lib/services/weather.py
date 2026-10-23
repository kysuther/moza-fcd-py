"""Weather (Open-Meteo, no account needed) and METAR/TAF (aviationweather.gov) for one city and airport.

The forecast refreshes every 15 minutes and the METAR/TAF every 10; refresh() fetches both now. If a
fetch fails, the last good data stays, with its age, so the pages can say how old it is."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from . import Worker
from .. import net

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
METAR_URL = "https://aviationweather.gov/api/data/metar"
TAF_URL = "https://aviationweather.gov/api/data/taf"
FORECAST_EVERY_S = 15 * 60
METAR_EVERY_S = 10 * 60
RETRY_S = 60

# WMO weather codes -> short text (max 11 characters)
WMO = {0: "CLEAR", 1: "MOSTLY CLR", 2: "PARTLY CLDY", 3: "OVERCAST", 45: "FOG", 48: "FREEZING FOG",
       51: "LT DRIZZLE", 53: "DRIZZLE", 55: "HVY DRIZZLE", 56: "FRZ DRIZZLE", 57: "FRZ DRIZZLE",
       61: "LIGHT RAIN", 63: "RAIN", 65: "HEAVY RAIN", 66: "FRZ RAIN", 67: "FRZ RAIN",
       71: "LIGHT SNOW", 73: "SNOW", 75: "HEAVY SNOW", 77: "SNOW GRAINS", 80: "SHOWERS", 81: "SHOWERS",
       82: "HVY SHOWERS", 85: "SNOW SHWRS", 86: "SNOW SHWRS", 95: "T-STORM", 96: "T-STORM HAIL", 99: "T-STORM HAIL"}
COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def condition(code) -> str:
    return WMO.get(int(code), "--") if code is not None else "--"


def compass(deg) -> str:
    return "--" if deg is None else COMPASS[int((float(deg) % 360) / 22.5 + 0.5) % 16]


def _hhmm(iso: str) -> str:
    return iso[11:16] if iso and len(iso) >= 16 else "--:--"


def parse_forecast(data: dict) -> dict:
    cur = data.get("current", {})
    units = data.get("current_units", {})
    daily = data.get("daily", {})
    hourly = data.get("hourly", {})
    days = []
    for i, day in enumerate(daily.get("time", [])):
        days.append({"date": day, "code": daily["weather_code"][i], "hi": daily["temperature_2m_max"][i],
                     "lo": daily["temperature_2m_min"][i],
                     "rain": (daily.get("precipitation_probability_max") or [None] * (i + 1))[i],
                     "sunrise": _hhmm(daily.get("sunrise", [""] * (i + 1))[i]),
                     "sunset": _hhmm(daily.get("sunset", [""] * (i + 1))[i])})
    hours = [{"time": _hhmm(t), "temp": hourly["temperature_2m"][i], "code": hourly["weather_code"][i],
              "rain": (hourly.get("precipitation_probability") or [None] * (i + 1))[i]}
             for i, t in enumerate(hourly.get("time", []))]
    return {"temp": cur.get("temperature_2m"), "feels": cur.get("apparent_temperature"),
            "humidity": cur.get("relative_humidity_2m"), "code": cur.get("weather_code"),
            "wind": cur.get("wind_speed_10m"), "wind_dir": cur.get("wind_direction_10m"),
            "gust": cur.get("wind_gusts_10m"), "is_day": bool(cur.get("is_day", 1)), "time": _hhmm(cur.get("time", "")),
            "temp_unit": "F" if "F" in units.get("temperature_2m", "°F") else "C",
            "wind_unit": "MPH" if "mp" in units.get("wind_speed_10m", "mp/h") else "KMH",
            "days": days, "hours": hours}


def parse_metar(data) -> Optional[dict]:
    if not data:
        return None
    m = data[0]
    clouds = m.get("clouds") or []
    layers = " ".join(f"{c.get('cover', '')}{int(c['base']) // 100:03d}" if c.get("base") is not None
                      else str(c.get("cover", "")) for c in clouds) or str(m.get("cover") or "")
    ceiling = None
    for c in clouds:
        if c.get("cover") in ("BKN", "OVC", "OVX") and c.get("base") is not None:
            ceiling = int(c["base"])
            break
    altim = m.get("altim")
    return {"station": m.get("icaoId", ""), "name": m.get("name", ""), "raw": m.get("rawOb", ""),
            "obs_time": m.get("obsTime"), "category": m.get("fltCat") or "", "temp": m.get("temp"),
            "dew": m.get("dewp"), "wdir": m.get("wdir"), "wspd": m.get("wspd"), "wgst": m.get("wgst"),
            "visib": m.get("visib"), "clouds": layers, "ceiling": ceiling,
            "altim_hpa": altim, "altim_inhg": round(altim / 33.8638866667, 2) if altim else None}


def parse_taf(data) -> Optional[dict]:
    if not data:
        return None
    t = data[0]
    return {"station": t.get("icaoId", ""), "raw": t.get("rawTAF", ""), "issued": t.get("issueTime", "")}


def zulu(epoch) -> str:
    if not epoch:
        return "--"
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%H%MZ")


class WeatherService(Worker):
    name = "weather"
    interval = 5.0

    def __init__(self, latitude: float, longitude: float, airport: str, units: str = "imperial",
                 fetch=net.get_json, wall_clock=time.time) -> None:
        super().__init__()
        self.latitude, self.longitude = latitude, longitude
        self.home_airport = airport.upper()
        self.airport = self.home_airport
        self.units = units
        self.fetch = fetch
        self.wall_clock = wall_clock
        self._next_forecast = 0.0
        self._next_metar = 0.0
        self.snapshot = {"forecast": None, "forecast_at": None, "metar": None, "taf": None, "metar_at": None,
                         "airport": self.airport, "error": ""}

    def poll(self) -> None:
        now = self.wall_clock()
        snap = dict(self.snapshot)
        if now >= self._next_forecast:
            try:
                snap["forecast"] = parse_forecast(self.fetch(FORECAST_URL, self._forecast_params()))
                snap["forecast_at"] = now
                snap["error"] = ""
                self._next_forecast = now + FORECAST_EVERY_S
            except Exception as e:
                snap["error"] = f"{type(e).__name__}: {e}"
                self._next_forecast = now + RETRY_S
        if now >= self._next_metar:
            try:
                params = {"ids": self.airport, "format": "json"}
                snap["metar"] = parse_metar(self.fetch(METAR_URL, params))
                snap["taf"] = parse_taf(self.fetch(TAF_URL, params))
                snap["metar_at"] = now
                snap["airport"] = self.airport
                self._next_metar = now + METAR_EVERY_S
            except Exception as e:
                snap["error"] = f"{type(e).__name__}: {e}"
                self._next_metar = now + RETRY_S
        self.snapshot = snap

    def _forecast_params(self) -> dict:
        imperial = self.units != "metric"
        return {"latitude": self.latitude, "longitude": self.longitude,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,"
                           "wind_speed_10m,wind_direction_10m,wind_gusts_10m,is_day",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                         "sunrise,sunset",
                "hourly": "temperature_2m,weather_code,precipitation_probability",
                "forecast_days": 4, "forecast_hours": 12, "timezone": "auto",
                "temperature_unit": "fahrenheit" if imperial else "celsius",
                "wind_speed_unit": "mph" if imperial else "kmh"}

    # --- actions ---------------------------------------------------------------------

    def refresh(self) -> None:
        self.do(self._refresh)

    def _refresh(self) -> None:
        self._next_forecast = self._next_metar = 0.0

    def set_airport(self, icao: Optional[str]) -> None:
        """Show another airport's METAR/TAF; None goes back to the home airport."""
        self.do(self._set_airport, (icao or self.home_airport).upper())

    def _set_airport(self, icao: str) -> None:
        self.airport = icao
        self._next_metar = 0.0
