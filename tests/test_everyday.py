"""Everyday app (Phases 1-3): parsers, timers, alerts, lights, the Windows/clipboard/weather/SimBrief/Discord
services and every page's key handling, with fake services (no network or Discord needed)."""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(HERE, "..", "apps", "everyday")
sys.path.insert(0, APP_DIR)
sys.path.insert(0, HERE)

import everyday  # noqa: E402
from everyday_lib import calc, util, winapi  # noqa: E402
from everyday_lib.app import Context, compute_alerts  # noqa: E402
from everyday_lib.config import DEFAULT_CONFIG, EXAMPLE_CONFIG_PATH, Settings, load_config  # noqa: E402
from everyday_lib.launcher import parse_items  # noqa: E402
from everyday_lib.notes import NotesStore  # noqa: E402
from everyday_lib.services import discord as dsc  # noqa: E402
from everyday_lib.services.clipboard import ClipboardService  # noqa: E402
from everyday_lib.services.simbrief import SimBriefService, parse_ofp  # noqa: E402
from everyday_lib.services.weather import (WeatherService, compass, condition, parse_forecast,  # noqa: E402
                                           parse_metar, parse_taf)
from everyday_lib.services.media import MediaService  # noqa: E402
from everyday_lib.services.system import parse_nvidia_smi, parse_ping  # noqa: E402
from everyday_lib.services.timers import Timers  # noqa: E402
from everyday_lib.services.windows import WindowsService  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeService:
    def __init__(self, snapshot=None, **extra):
        self.snapshot = snapshot or {}
        self.available = True
        self.error = ""
        self.calls = []
        self.__dict__.update(extra)

    def __getattr__(self, name):          # any action method records its call
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a: self.calls.append((name,) + a)


class Screen:
    def __init__(self):
        self.page, self.lights, self.brightness, self.on_key = None, {}, [], None
        self.levels = {"screen": 73, "keys": 8, "lamps": 6}   # what the device reports
        self.level_writes = []
        self.sensor_writes = []

    def enable_light_sensor(self, on):
        self.sensor_writes.append(on); return True

    def set_screen_brightness(self, v):
        self.level_writes.append(("screen", v)); self.levels["screen"] = v; return True

    def set_key_backlight(self, v):
        self.level_writes.append(("keys", v)); self.levels["keys"] = v; return True

    def set_indicator_brightness(self, v):
        self.level_writes.append(("lamps", v)); self.levels["lamps"] = v; return True

    def screen_brightness(self):
        return self.levels["screen"]

    def key_backlight(self, timeout=1.0):
        return self.levels["keys"]

    def indicator_brightness(self, timeout=1.0):
        return self.levels["lamps"]

    def show(self, page):
        self.page = page

    def set_light(self, name, on):
        self.lights[name] = on
        return True

    def set_brightness(self, value, group=0):
        self.brightness.append(value)
        return True

    def press(self, *names):
        for n in names:
            self.on_key(n, True)
            self.on_key(n, False)

    def row(self, r):
        return self.page.row_text(r)

    def text(self):
        return self.page.to_text()


AUDIO = {"volume": 42, "muted": False, "mic_muted": False,
         "apps": [{"key": f"app{i}", "name": f"APP{i}", "volume": 50, "muted": False} for i in range(7)],
         "devices": [{"id": "d1", "name": "Speakers (Realtek)", "default": True},
                     {"id": "d2", "name": "Headset", "default": False}]}
SYSTEM = {"cpu": 12.0, "ram": 40.0, "ram_used": 12.4, "ram_total": 31.0, "uptime": 3700,
          "gpu": {"name": "RTX", "util": 8.0, "temp": 40.0, "vram_used": 2.8, "vram_total": 16.0},
          "ping": 12.0, "net_down": 600000, "net_up": 38000,
          "disks": [{"name": "C:", "total": 930, "free": 313, "free_pct": 34}],
          "procs": [{"pid": 100 + i, "name": f"proc{i}.exe", "cpu": 5.0 - i} for i in range(5)]}
WINDOWS = {"plans": [{"guid": "381b4222-f694-41f0-9685-ff5bb260df2e", "name": "Balanced", "active": True},
                     {"guid": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", "name": "High performance", "active": False}],
           "keep_awake": False, "dark": False, "pending": None, "failed": None}
CLIPBOARD = {"text": "Hello world from the clipboard", "history": ["Hello world from the clipboard", "older clip",
                                                                   "oldest clip"]}
FORECAST_JSON = {
    "current_units": {"temperature_2m": "°F", "wind_speed_10m": "mp/h"},
    "current": {"time": "2026-10-21T17:15", "temperature_2m": 72.0, "apparent_temperature": 70.7,
                "relative_humidity_2m": 44, "weather_code": 0, "wind_speed_10m": 12.3, "wind_direction_10m": 197,
                "wind_gusts_10m": 21.0, "is_day": 1},
    "hourly": {"time": [f"2026-10-21T{h}:00" for h in range(17, 24)], "temperature_2m": [72, 68, 64, 61, 60, 58, 55],
               "weather_code": [0] * 7, "precipitation_probability": [0] * 7},
    "daily": {"time": ["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24"], "weather_code": [0, 3, 61, 73],
              "temperature_2m_max": [75, 66, 58, 40], "temperature_2m_min": [50, 48, 45, 30],
              "precipitation_probability_max": [0, 10, 80, 60], "sunrise": ["2026-10-21T07:31"] * 4,
              "sunset": ["2026-10-21T18:22"] * 4}}
METAR_JSON = [{"icaoId": "KORD", "obsTime": 1792619580, "temp": 22.8, "dewp": 10, "wdir": 0, "wspd": 0,
               "visib": "10+", "altim": 1021.1, "rawOb": "METAR KORD 212153Z 00000KT 10SM CLR 23/10 A3015 RMK AO2",
               "name": "Chicago O'Hare Intl", "cover": "CLR", "clouds": [], "fltCat": "VFR"}]
TAF_JSON = [{"icaoId": "KORD", "issueTime": "2026-10-21T20:57:00.000Z",
             "rawTAF": "TAF KORD 212057Z 2121/2224 VRB03KT P6SM SKC FM220600 07004KT P6SM SKC TEMPO 2210/2212 BKN020"}]
OFP_JSON = {
    "fetch": {"status": "Success"},
    "general": {"icao_airline": "SWA", "flight_number": "2973", "costindex": "7", "initial_altitude": "34000",
                "route_distance": "557", "passengers": "145", "route": "DEZRT2 MVA DCT RPARK OAKES3"},
    "origin": {"icao_code": "KSLC", "name": "SALT LAKE CITY INTL", "plan_rwy": "34L",
               "metar": "KSLC 202154Z 28016KT 10SM FEW080 22/09 A3011", "taf": "KSLC ..."},
    "destination": {"icao_code": "KOAK", "plan_rwy": "30", "metar": "KOAK 202153Z 30012KT 10SM 19/10 A2985"},
    "alternate": {"icao_code": "KSMF", "plan_rwy": "35R", "faa_code": {}},
    "aircraft": {"icaocode": "B38M", "name": "B737 MAX 8", "reg": "B-2131"},
    "fuel": {"plan_ramp": "12967", "plan_takeoff": "12545", "enroute_burn": "7296", "taxi": "422",
             "contingency": "378", "alternate_burn": "1905", "reserve": "2966", "extra": "0", "plan_landing": "5249"},
    "weights": {"est_zfw": "133870", "max_zfw": "145400", "est_tow": "146415", "max_tow": "160096",
                "est_ldw": "139119", "max_ldw": "152800", "payload": "34510", "pax_count": "145", "cargo": "6380",
                "oew": "99360"},
    "times": {"est_time_enroute": "5787", "sched_out": "1792539000", "sched_in": "1792546800"},
    "params": {"units": "lbs", "airac": "2610", "time_generated": "1792536958"},
    "atc": {"callsign": "SWA2973"}}
DISCORD = {"configured": True, "state": "CONNECTED", "user": "pilot1", "mute": False, "deaf": False,
           "channel": {"name": "General", "members": [{"name": "pilot1", "mute": False, "deaf": False},
                                                      {"name": "friend", "mute": True, "deaf": False}]},
           "notifications": [{"title": "friend (#general, Server)", "body": "Ready for the flight?", "at": 0}],
           "unseen": 0, "error": ""}
MEDIA = {"active": True, "title": "A very long song title that needs two lines", "artist": "Artist",
         "status": "Playing", "position": 30, "duration": 200, "received_at": 0}


def make(clock=None, audio=None, system=None, media=None, settings=None):
    clock = clock or Clock()
    ctx = Context(config=load_config(EXAMPLE_CONFIG_PATH), settings=settings or Settings(path=None),
                  audio=FakeService(dict(audio or AUDIO)),
                  media=FakeService(dict(media or MEDIA), position=lambda now=None: 30.0),
                  system=FakeService(dict(system or SYSTEM), ping_host="1.1.1.1"),
                  timers=Timers(25, 5, clock=clock), clock=clock,
                  windows=FakeService(dict(WINDOWS)), clipboard=FakeService(dict(CLIPBOARD)),
                  notes=NotesStore(path=None),
                  weather=FakeService({"forecast": parse_forecast(FORECAST_JSON), "forecast_at": 0,
                                       "metar": parse_metar(METAR_JSON), "taf": parse_taf(TAF_JSON),
                                       "metar_at": 0, "airport": "KORD", "error": ""}),
                  simbrief=FakeService({"plan": parse_ofp(OFP_JSON), "fetched_at": 0, "error": "",
                                        "fetching": False, "configured": True}),
                  discord=FakeService(dict(DISCORD)))
    screen = Screen()
    app = everyday.build(screen, ctx)
    app.go("menu")
    return screen, app, ctx, clock


class UtilTests(unittest.TestCase):
    def test_clean_and_wrap(self):
        self.assertEqual(util.clean("🔥 Fénix  v3!"), "FENIX V3!")
        self.assertEqual(util.wrap2("FENIX V3 IS OUT! MIAMI KEYWEST KMIA"), ("FENIX V3 IS OUT! MIAMI", "KEYWEST KMIA"))
        self.assertEqual(util.wrap2("SHORT"), ("SHORT", ""))

    def test_parsers(self):
        self.assertEqual(util.parse_duration("25"), 1500)
        self.assertEqual(util.parse_duration("1:30"), 90)
        self.assertEqual(util.parse_duration("90S"), 90)
        for bad in ("0", "1:75", "ABC", ""):
            self.assertIsNone(util.parse_duration(bad), bad)
        self.assertEqual(util.parse_percent("100"), 100)
        self.assertIsNone(util.parse_percent("101"))
        self.assertEqual(util.mmss(3725), "1:02:05")
        self.assertEqual(util.rate(600000), "0.6M")
        self.assertEqual(util.bar(50), "#####-----")

    def test_processes_page_never_offers_this_app(self):
        import psutil
        from everyday_lib.services.system import SystemService
        svc = SystemService(ping_host="127.0.0.1")
        protected = svc.protected_pids(psutil)
        self.assertIn(os.getpid(), protected)
        self.assertIn(os.getppid(), protected)                 # e.g. the venv launcher / terminal
        self.assertTrue(all(r["pid"] not in protected for r in svc._top_processes(psutil, count=500)))

    def test_service_parsers(self):
        g = parse_nvidia_smi("NVIDIA GeForce RTX 4080 SUPER, 22, 34, 2897, 16376\n")
        self.assertEqual((g["util"], g["temp"], round(g["vram_total"])), (22.0, 34.0, 16))
        self.assertIsNone(parse_nvidia_smi(""))
        self.assertEqual(parse_ping("Reply from 1.1.1.1: bytes=32 time=12ms TTL=57"), 12.0)
        self.assertEqual(parse_ping("Reply from 1.1.1.1: bytes=32 time<1ms TTL=57"), 1.0)
        self.assertIsNone(parse_ping("Request timed out."))

    def test_config_defaults_and_example_file(self):
        cfg = load_config(EXAMPLE_CONFIG_PATH)
        self.assertEqual(cfg["weather"]["metar_airport"], "KORD")
        self.assertEqual(cfg["weather"]["city"], "Chicago")
        self.assertEqual((cfg["discord"]["client_secret"], cfg["simbrief"]["userid"]), ("", ""))  # no secrets shipped
        self.assertEqual(load_config("does-not-exist.toml")["alerts"], DEFAULT_CONFIG["alerts"])


class TimerTests(unittest.TestCase):
    def test_countdown_pause_resume_done(self):
        c = Clock()
        t = Timers(clock=c)
        t.countdown.set(60)
        t.countdown.start()
        c.t += 20
        t.countdown.pause()
        c.t += 100
        self.assertAlmostEqual(t.countdown.remaining(), 40)
        t.countdown.start()
        c.t += 40
        self.assertEqual(t.tick(), ["TIMER DONE: ANY KEY"])
        self.assertEqual(t.alarm, "TIMER DONE: ANY KEY")
        self.assertTrue(t.acknowledge())
        self.assertIsNone(t.alarm)

    def test_stopwatch_and_pomodoro(self):
        c = Clock()
        t = Timers(focus_min=1, break_min=1, clock=c)
        t.stopwatch.toggle(); c.t += 5; t.stopwatch.toggle(); c.t += 5
        self.assertAlmostEqual(t.stopwatch.elapsed(), 5)
        t.pomodoro.toggle()
        c.t += 61
        self.assertEqual(t.tick(), ["BREAK TIME: ANY KEY"])
        self.assertEqual((t.pomodoro.phase, t.pomodoro.count, t.pomodoro.running), ("BREAK", 1, True))
        t.pomodoro.skip()
        self.assertEqual(t.pomodoro.phase, "FOCUS")


class AlertTests(unittest.TestCase):
    def test_alerts_and_msg_light(self):
        screen, app, ctx, clock = make()
        self.assertEqual(compute_alerts(ctx), [])
        ctx.system.snapshot = dict(SYSTEM, gpu=dict(SYSTEM["gpu"], temp=90.0), ram=95.0,
                                   disks=[{"name": "C:", "total": 930, "free": 20, "free_pct": 2}])
        self.assertEqual(compute_alerts(ctx), ["RAM HIGH 95%", "GPU HOT 90°C", "DISK C: LOW 2%"])
        app.refresh()
        self.assertTrue(screen.lights["MSG"])
        self.assertIn("RAM HIGH 95%", screen.text())     # footer on the menu

    def test_cpu_alert_needs_to_be_sustained(self):
        screen, app, ctx, clock = make()
        ctx.system.snapshot = dict(SYSTEM, cpu=99.0)
        self.assertEqual(compute_alerts(ctx), [])
        clock.t += 31
        self.assertEqual(compute_alerts(ctx), ["CPU HIGH 99%"])
        ctx.system.snapshot = dict(SYSTEM, cpu=10.0)
        self.assertEqual(compute_alerts(ctx), [])


class LightTests(unittest.TestCase):
    def test_timer_alarm_flashes_call_and_any_key_acknowledges(self):
        screen, app, ctx, clock = make()
        ctx.timers.countdown.set(10)
        ctx.timers.countdown.start()
        clock.t = 1010.0
        app.refresh()
        self.assertEqual(screen.row(13).strip(), "TIMER DONE: ANY KEY")
        self.assertTrue(screen.lights["CALL"])
        clock.t += 0.5
        app.refresh()
        self.assertFalse(screen.lights["CALL"])          # flashing
        self.assertFalse(screen.lights["MSG"])           # an alarm is not an alert
        screen.press("LSK2L")                            # acknowledges only - doesn't open AUDIO
        self.assertIn("MENU", screen.row(0))
        self.assertIsNone(ctx.timers.alarm)
        self.assertEqual(screen.row(13).strip(), "")
        clock.t = 1011.0
        app.refresh()
        self.assertFalse(screen.lights["CALL"])

    def test_fail_light_follows_mic_mute(self):
        screen, app, ctx, clock = make()
        app.refresh()
        self.assertFalse(screen.lights["FAIL"])
        ctx.audio.snapshot = dict(AUDIO, mic_muted=True)
        app.refresh()
        self.assertTrue(screen.lights["FAIL"])

    def test_lights_only_sent_on_change(self):
        screen, app, ctx, clock = make()
        sent = []
        screen.set_light = lambda n, on: sent.append((n, on)) or True
        app._light_state.clear()
        app.refresh()
        first = len(sent)
        app.refresh()
        self.assertEqual(len(sent), first)

    def test_brightness_keys_adjust_screen(self):
        screen, app, ctx, clock = make()
        app.read_levels()
        self.assertEqual(ctx.levels, {"screen": 73, "keys": 8, "lamps": 6})
        screen.press("BRT+")
        self.assertEqual(screen.level_writes[-1], ("screen", 83))
        screen.press("BRT+", "BRT+")
        self.assertEqual(screen.level_writes[-1], ("screen", 100))      # clamped
        screen.press("BRT-")
        self.assertEqual(screen.row(13).strip(), "SCREEN BRIGHTNESS 90%")


class PageTests(unittest.TestCase):
    def setUp(self):
        self.s, self.app, self.ctx, self.clock = make()

    def test_menu_navigation(self):
        self.s.press("LSK1L")
        self.assertIn("DASHBOARD", self.s.row(0))
        self.s.press("MENU")
        grey = 8                                          # every menu entry is built now
        self.assertNotIn(grey, {self.s.page[r, c].color for r in range(1, 13) for c in range(24)
                                if self.s.page[r, c].char != " "})
        for key, title in (("INIT_REF", "DASHBOARD"), ("RTE", "AUDIO"), ("CLB", "MEDIA"), ("CRZ", "SYSTEM"),
                           ("HOLD", "CLOCK"), ("N1_LIMIT", "SETTINGS"), ("MENU", "MENU")):
            self.s.press(key)
            self.assertIn(title, self.s.row(0), key)

    def test_dashboard_contents(self):
        self.s.press("INIT_REF")
        t = self.s.text()
        for expected in ("LOCAL TIME", "DATE", "UTC", "Z ", "A VERY LONG SONG TITLE", "CPU 12 RAM 40", "GPU 8 40°",
                         "DN 0.6M"):
            self.assertIn(expected, t)

    def test_audio_actions(self):
        self.s.press("RTE", "LSK1R", "LSK2L", "LSK2R", "LSK3R")
        self.s.press("7", "5", "LSK1L")
        self.s.press("1", "5", "0", "LSK1L")
        self.assertEqual(self.s.row(13).strip(), "INVALID ENTRY")
        self.assertEqual(self.ctx.audio.calls, [("toggle_mute",), ("step_volume", -5), ("step_volume", 5),
                                                ("toggle_mic",), ("set_volume", 75)])

    def test_mixer_paging_and_actions(self):
        self.s.press("RTE", "LSK4L")
        self.assertIn("1/2", self.s.row(0))
        self.s.press("LSK1L", "3", "0", "LSK2L")
        self.s.press("NEXT_PAGE")
        self.assertIn("APP5", self.s.text())
        self.s.press("LSK1L")
        self.assertEqual(self.ctx.audio.calls, [("toggle_app_mute", "app0"), ("set_app_volume", "app1", 30),
                                                ("toggle_app_mute", "app5")])

    def test_output_switch(self):
        self.s.press("RTE", "LSK4R", "LSK2L")
        self.assertEqual(self.ctx.audio.calls, [("set_default_device", "d2")])

    def test_media_controls_and_wrapped_title(self):
        self.s.press("CLB")
        self.assertTrue(self.s.row(2).startswith("A VERY LONG SONG TITLE"))
        self.assertTrue(self.s.row(4).startswith("THAT NEEDS TWO LINES"))
        self.s.press("LSK5L", "LSK5R", "LSK6R", "EXEC")
        self.assertEqual(self.ctx.media.calls, [("previous",), ("next",), ("play_pause",), ("play_pause",)])

    def test_media_position_interpolates(self):
        m = MediaService(key_sender=lambda vk: None)
        m.feed('{"active":true,"status":"Playing","position":30,"duration":200}', now=100.0)
        self.assertAlmostEqual(m.position(now=105.0), 35.0)
        m.feed('{"active":true,"status":"Paused","position":30,"duration":200}', now=100.0)
        self.assertAlmostEqual(m.position(now=105.0), 30.0)

    def test_system_pages_and_end_process_needs_exec(self):
        self.s.press("CRZ")
        self.assertIn("12%", self.s.row(2))
        self.s.press("NEXT_PAGE")
        self.assertIn("313G FREE", self.s.text())
        self.s.press("LSK6R", "LSK2L")
        self.assertTrue(self.app.exec_pending)
        self.assertIn("EXEC ENDS PROC1.EXE", self.s.text())
        self.assertEqual(self.ctx.system.calls, [])
        self.s.press("EXEC")
        self.assertEqual(self.ctx.system.calls, [("terminate", 101)])
        self.s.press("LSK3L", "LSK6L")                    # select, then cancel
        self.assertFalse(self.app.exec_pending)
        self.assertEqual(len(self.ctx.system.calls), 1)

    def test_clock_and_timers(self):
        self.s.press("HOLD")
        self.assertIn("LOCAL", self.s.text())
        self.assertIn("UTC", self.s.text())
        self.s.press("LSK6R")                             # TIMERS>
        self.s.press("5", "LSK1L", "LSK1R")
        self.assertTrue(self.ctx.timers.countdown.running)
        self.clock.t += 60
        self.app.refresh()
        self.assertIn("04:00", self.s.text())
        self.s.press("LSK3R")
        self.assertTrue(self.ctx.timers.stopwatch.running)
        self.s.press("LSK5R", "LSK6R")                    # start Pomodoro, then skip
        self.assertEqual(self.ctx.timers.pomodoro.phase, "BREAK")

    def test_settings_brightness_and_start_page(self):
        self.app.read_levels()
        self.s.press("N1_LIMIT")
        t = self.s.text()
        for expected in ("SCREEN 73%", "KEY BACKLIGHT 8/15", "ANNUNCIATORS 6/15"):
            self.assertIn(expected, t)
        self.s.press("6", "0", "LSK1L")                  # typed value sets screen %
        self.s.press("LSK2R", "LSK3L")                   # keys +1, lamps -1
        self.s.press("2", "0", "LSK2L")                  # out of range for /15
        self.assertEqual(self.s.row(13).strip(), "ENTER 0-15")
        self.assertEqual(self.s.level_writes, [("screen", 60), ("keys", 9), ("lamps", 5)])
        self.s.press("CLR", "CLR", "CLR", "NEXT_PAGE", "LSK1L")       # start page is on page 2
        self.assertEqual(self.ctx.settings["start_page"], "dashboard")

    def test_unavailable_services_degrade(self):
        self.ctx.audio.available = False
        self.ctx.media.available = False
        self.s.press("RTE")
        self.assertIn("AUDIO NOT AVAILABLE", self.s.text())
        self.s.press("CLB")
        self.assertIn("MEDIA NOT AVAILABLE", self.s.text())

    def test_every_page_renders_24_wide(self):
        for name in self.app.pages:
            self.app.go(name)
            for r in range(14):
                self.assertEqual(len(self.s.row(r)), 24, name)

    def test_messages_and_labels_fit(self):
        lib = os.path.join(APP_DIR, "everyday_lib")
        literals = []
        for root, _, files in os.walk(lib):
            for f in files:
                if f.endswith(".py"):
                    with open(os.path.join(root, f), encoding="utf-8") as fh:
                        src = fh.read()
                    literals += re.findall(r'show_message\(\s*"([^"{]+)"', src)
                    literals += re.findall(r'(?:left|right)_label="([^"{]+)"', src)
        self.assertTrue(literals)
        for text in literals:
            self.assertLessEqual(len(text), 23, text)     # labels start at column 1


class Phase2ParserTests(unittest.TestCase):
    def test_calculator(self):
        ev = calc.evaluate
        self.assertEqual(ev("12+3X4"), 24)
        self.assertEqual(ev("10/4"), 2.5)
        self.assertEqual(ev("-5+2"), -3)
        self.assertEqual(ev("2X-3"), -6)
        self.assertEqual(ev("+5", ans=10), 15)
        self.assertEqual(ev("X2", ans=3), 6)
        self.assertEqual(ev("-1", ans=3), 2)
        self.assertEqual(ev("", ans=7), 7)
        for bad, msg in (("1/0", "DIVIDE BY ZERO"), ("5+", "INVALID ENTRY"), ("X2", "INVALID ENTRY"),
                         ("1.2.3", "INVALID ENTRY"), ("", "ENTER A CALCULATION"), ("ABC", "INVALID ENTRY")):
            with self.assertRaises(calc.CalcError, msg=bad) as cm:
                ev(bad)
            self.assertEqual(str(cm.exception), msg)
        self.assertEqual(calc.fmt(0.1 + 0.2), "0.3")
        self.assertEqual(calc.fmt(-0.0), "0")
        self.assertEqual(calc.fmt(1 / 3), "0.3333333333")
        self.assertEqual(calc.fmt(2 ** 60), "1.1529E18")

    def test_conversions(self):
        self.assertEqual(calc.convert(0, False, 212), ("°F", "°C", 100.0))
        self.assertAlmostEqual(calc.convert(0, True, 0)[2], 32.0)
        self.assertAlmostEqual(calc.convert(8, False, 1)[2], 1.852)
        self.assertAlmostEqual(calc.convert(9, True, 1013.25)[2], 29.92, places=2)
        self.assertEqual(len(calc.CONVERSIONS), 10)

    def test_chords_and_steps(self):
        self.assertEqual(winapi.parse_chord("ctrl+shift+m"), (0x11, 0x10, 0x4D))
        self.assertEqual(winapi.parse_chord("WIN+PERIOD"), (0x5B, 0xBE))
        for bad in ("CTRL+", "HYPER+X", "CTRL+SHIFT", ""):
            with self.assertRaises(ValueError, msg=bad):
                winapi.parse_chord(bad)
        self.assertEqual(winapi.parse_steps(["CTRL+C", "wait 0.5", "ALT+TAB"]),
                         [("chord", (0x11, 0x43)), ("wait", 0.5), ("chord", (0x12, 0x09))])
        with self.assertRaises(ValueError):
            winapi.parse_steps("WAIT 30")
        slept = []
        sent = []
        orig = winapi.send_chord
        winapi.send_chord = sent.append
        try:
            winapi.run_steps([("chord", (1,)), ("wait", 0.5)], sleep=slept.append)
        finally:
            winapi.send_chord = orig
        self.assertEqual((sent, slept), ([(1,)], [0.03, 0.5]))

    def test_powercfg(self):
        out = ("Existing Power Schemes (* Active)\n-----------------------------------\n"
               "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *\n"
               "Power Scheme GUID: 8C5E7FDA-E8BF-4A96-9A85-A6E23A8C635C  (High performance)\n")
        self.assertEqual(winapi.parse_powercfg(out), [
            {"guid": "381b4222-f694-41f0-9685-ff5bb260df2e", "name": "Balanced", "active": True},
            {"guid": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c", "name": "High performance", "active": False}])
        with self.assertRaises(ValueError):
            winapi.set_power_plan("not-a-guid & del")

    def test_launcher_items(self):
        items = parse_items([{"label": "A really long label", "run": "x.exe", "args": "--flag"},
                             {"label": "Snip", "keys": "WIN+SHIFT+S", "confirm": True},
                             {"label": "Bad", "keys": "CTRL+NOPE"}, {"label": "Hi", "text": "hello\n"},
                             {"label": "Empty"}, "not a table"])
        self.assertEqual([i.label for i in items], ["A REALLY LO", "SNIP", "BAD", "HI", "EMPTY"])
        self.assertEqual([i.kind_label for i in items], ["OPEN", "KEYS", "BAD KEYS", "TYPE", "NO ACTION"])
        self.assertEqual(items[0].args, ["--flag"])
        self.assertTrue(items[1].confirm)
        self.assertEqual(len(parse_items(DEFAULT_CONFIG["launcher"]["items"])), 5)
        shipped = parse_items(load_config(EXAMPLE_CONFIG_PATH)["launcher"]["items"])
        self.assertTrue(shipped and not any(i.error for i in shipped))

    def test_notes_store_persists(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notes.json")
            n = NotesStore(path)
            self.assertTrue(n.add("FIRST"))
            n.add("SECOND")
            n.replace(0, "FIRST EDITED")
            n.delete(1)
            self.assertFalse(n.add("   "))
            self.assertEqual(NotesStore(path).notes, ["FIRST EDITED"])

    def test_wrap_lines(self):
        self.assertEqual(util.wrap_lines("one two three", 7), ["ONE TWO", "THREE"])
        self.assertEqual(util.wrap_lines("x" * 30, 24), ["X" * 24, "X" * 6])
        self.assertEqual(util.wrap_lines("", 24), [])


class FakeApi:
    def __init__(self):
        self.calls, self.seq, self.text = [], 1, "a"

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a: self.calls.append((name,) + a)

    def clipboard_sequence(self):
        return self.seq

    def get_clipboard_text(self):
        return self.text

    def power_plans(self):
        return [{"guid": "g", "name": "Balanced", "active": True}]

    def dark_mode(self):
        return True


class Phase2ServiceTests(unittest.TestCase):
    def test_clipboard_history(self):
        api = FakeApi()
        c = ClipboardService(api=api)
        for seq, text in ((1, "a"), (1, "ignored"), (2, "b"), (3, "a"), (4, ""), (5, None)):
            api.seq, api.text = seq, text
            c.poll()
        self.assertEqual(c.snapshot, {"text": "", "history": ["a", "b"]})
        api.seq, api.text = 6, "c"         # the failed read (None) is retried on the next change
        c.poll()
        self.assertEqual(c.snapshot["history"], ["c", "a", "b"])

    def test_windows_service(self):
        api, now = FakeApi(), [1000.0]
        w = WindowsService(api=api, wall_clock=lambda: now[0])
        w._shutdown("restart", 60)
        w._set_keep_awake(True)
        w.poll()
        self.assertEqual(w.snapshot["pending"], {"kind": "restart", "at": 1060.0})
        self.assertTrue(w.snapshot["keep_awake"])
        self.assertEqual(api.calls, [("schedule_shutdown", "restart", 60), ("set_keep_awake", True)])
        now[0] = 1070.0
        w.poll()
        self.assertIsNone(w.snapshot["pending"])
        w._try("OPEN FAILED", lambda: 1 / 0)
        w.poll()
        self.assertEqual(w.snapshot["failed"], "OPEN FAILED")
        w.teardown()
        self.assertEqual(api.calls[-1], ("set_keep_awake", False))


class Phase2PageTests(unittest.TestCase):
    def setUp(self):
        self.s, self.app, self.ctx, self.clock = make()

    def test_menu_and_function_keys_reach_new_pages(self):
        for key, title in (("LSK1R", "LAUNCHER"), ("LSK2R", "TOOLS"), ("LSK6L", "WINDOWS")):
            self.s.press("MENU", key)
            self.assertIn(title, self.s.row(0), key)
        for key, title in (("LEGS", "LAUNCHER"), ("DEP_ARR", "WINDOWS"), ("PROG", "TOOLS"), ("FIX", "CALCULATOR")):
            self.s.press(key)
            self.assertIn(title, self.s.row(0), key)

    def test_launcher_runs_items_and_confirm_needs_exec(self):
        page = self.app.pages["launcher"]
        page.items = parse_items([{"label": "EXPLORER", "run": "explorer.exe"},
                                  {"label": "SNIP", "keys": "WIN+SHIFT+S"},
                                  {"label": "HELLO", "text": "hi"},
                                  {"label": "SIGN OUT", "run": "shutdown.exe", "args": ["/l"], "confirm": True},
                                  {"label": "BAD", "keys": "NOPE"}, {"label": "SIX", "run": "six.exe"}])
        self.s.press("LEGS")
        self.assertTrue(self.s.row(2).startswith("<EXPLORER"))
        self.assertTrue(self.s.row(2).endswith("SIX>"))           # 6th item starts the right column
        self.s.press("LSK1L", "LSK2L", "LSK3L")
        self.assertIn("TYPED HELLO", self.s.row(11))
        self.s.press("LSK4L")
        self.assertTrue(self.app.exec_pending)
        self.assertIn("EXEC TO RUN SIGN OUT", self.s.text())
        self.assertEqual(len(self.ctx.windows.calls), 3)
        self.s.press("EXEC")
        self.s.press("LSK5L")
        self.assertEqual(self.s.row(13).strip(), "BAD BAD KEYS")
        w = self.ctx.windows.calls
        self.assertEqual(w[0], ("launch", "explorer.exe", []))
        self.assertEqual(w[1], ("send_keys", [("chord", (0x5B, 0x10, 0x53))]))
        self.assertEqual(w[2], ("type_text", "hi"))
        self.assertEqual(w[3], ("launch", "shutdown.exe", ["/l"]))
        self.ctx.windows.snapshot["failed"] = "OPEN FAILED"
        self.app.refresh()
        self.assertIn("OPEN FAILED", self.s.row(11))

    def test_windows_controls(self):
        self.s.press("DEP_ARR", "LSK1L", "LSK1R", "LSK2R", "LSK4L", "LSK4R", "LSK5L")
        names = [c[0] for c in self.ctx.windows.calls]
        self.assertEqual(names, ["lock", "monitors_off", "toggle_keep_awake", "send_keys", "send_keys",
                                 "toggle_dark_mode"])
        self.assertIn("<DARK MODE", self.s.text())
        self.assertIn("THEME LIGHT", self.s.text())

    def test_sleep_restart_shutdown_need_exec_and_can_abort(self):
        self.s.press("DEP_ARR", "LSK2L")
        self.assertTrue(self.app.exec_pending)
        self.assertIn("EXEC TO SLEEP PC", self.s.text())
        self.s.press("LSK6L")                                      # cancel
        self.assertFalse(self.app.exec_pending)
        self.s.press("LSK3R", "EXEC")
        self.assertEqual(self.ctx.windows.calls, [("shutdown", "shutdown", 60)])
        self.ctx.windows.snapshot["pending"] = {"kind": "shutdown", "at": 10 ** 12}
        self.app.refresh()
        self.assertIn("ABORT>", self.s.row(10))
        self.assertIn("SHUTDOWN IN", self.s.row(11))
        self.s.press("LSK5R")
        self.assertEqual(self.ctx.windows.calls[-1], ("abort_shutdown",))

    def test_power_plans(self):
        self.s.press("DEP_ARR", "LSK6R")
        self.assertIn("2/2", self.s.row(0))
        self.assertIn("ACTIVE", self.s.row(1))
        self.s.press("LSK2L")
        self.assertEqual(self.ctx.windows.calls, [("set_power_plan", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c")])
        self.assertIn("POWER PLAN HIGH PERFORM", self.s.row(11))

    def test_calculator(self):
        self.s.press("FIX", "1", "2", "+/-", "+/-", "3", "X", "4", "LSK6R")
        self.assertTrue(self.s.row(2).startswith("<24"))
        self.assertIn("12+3X4", self.s.row(1))
        self.s.press("+/-", "+/-", "1", "EXEC")                    # continues from the answer
        self.assertTrue(self.s.row(2).startswith("<25"))
        self.s.press("LSK1R")                                       # SQRT of the answer
        self.assertTrue(self.s.row(2).startswith("<5"))
        self.s.press("1", "/", "0", "EXEC")
        self.assertEqual(self.s.row(13).strip(), "DIVIDE BY ZERO")
        self.s.press("CLR", "CLR", "CLR", "CLR")
        self.s.press("LSK2L")                                       # recall 25
        self.assertEqual(self.app.scratchpad, "25")
        self.s.press("LSK5R")
        self.assertEqual(self.ctx.clipboard.calls, [("set_text", "5")])
        self.s.press("LSK5L")
        self.assertIn("TYPE 12+3X4", self.s.text())

    def test_converter(self):
        self.s.press("PROG", "LSK2L", "2", "1", "2", "LSK1L")
        self.assertIn("212 °F = 100 °C", self.s.row(11))
        self.s.press("LSK1R")                                       # chain: 100 °C back to °F
        self.assertIn("100 °C = 212 °F", self.s.row(11))
        self.s.press("NEXT_PAGE", "1", "LSK4L")
        self.assertIn("1 NM = 1.85 KM", self.s.row(11))

    def test_notes(self):
        self.s.press("PROG", "LSK3L", "A", "B", "LSK1L", "C", "D", "LSK2L")
        self.assertEqual(self.ctx.notes.notes, ["AB", "CD"])
        self.s.press("LSK1L")                                       # copy into the scratchpad to edit
        self.assertEqual(self.app.scratchpad, "AB")
        self.s.press("X", "LSK1L")
        self.assertEqual(self.ctx.notes.notes, ["ABX", "CD"])
        self.s.press("LSK2R")
        self.assertEqual(self.ctx.clipboard.calls, [("set_text", "CD")])
        self.s.press("DEL", "LSK1L")
        self.assertEqual(self.ctx.notes.notes, ["CD"])
        self.assertEqual(self.app.message, "")
        for ch in "EFGH":
            self.s.press(ch, "LSK2L" if ch == "E" else f"LSK{len(self.ctx.notes) + 1}L")
        self.assertEqual(len(self.ctx.notes), 5)
        self.assertIn("1/2", self.s.row(0))                         # the empty line moved to page 2

    def test_clipboard(self):
        self.s.press("PROG", "LSK4L")
        self.assertIn("HELLO WORLD FROM THE", self.s.text())
        self.s.press("H", "I", "LSK5R", "LSK5L")
        self.assertEqual(self.ctx.clipboard.calls, [("set_text", "HI"), ("clear",)])
        self.s.press("LSK6R")
        self.assertTrue(self.s.row(2).startswith("<OLDER CLIP"))
        self.s.press("LSK2L")
        self.assertEqual(self.ctx.clipboard.calls[-1], ("set_text", "oldest clip"))

    def test_phase2_pages_degrade_without_windows(self):
        self.ctx.windows.available = False
        self.ctx.clipboard.available = False
        self.s.press("DEP_ARR")
        self.assertIn("WINDOWS CONTROLS N/A", self.s.text())
        self.s.press("LSK1L")
        self.assertEqual(self.ctx.windows.calls, [])
        self.s.press("PROG", "LSK4L")
        self.assertIn("CLIPBOARD N/A", self.s.text())


class Phase3ParserTests(unittest.TestCase):
    def test_forecast(self):
        f = parse_forecast(FORECAST_JSON)
        self.assertEqual((f["temp"], f["temp_unit"], f["wind_unit"], f["time"]), (72.0, "F", "MPH", "17:15"))
        self.assertEqual(f["days"][2], {"date": "2026-10-23", "code": 61, "hi": 58, "lo": 45, "rain": 80,
                                        "sunrise": "07:31", "sunset": "18:22"})
        self.assertEqual(f["hours"][1]["temp"], 68)
        self.assertEqual((condition(0), condition(73), condition(None), condition(42)), ("CLEAR", "SNOW", "--", "--"))
        self.assertEqual((compass(197), compass(359), compass(0), compass(None)), ("SSW", "N", "N", "--"))

    def test_metar_and_taf(self):
        m = parse_metar(METAR_JSON)
        self.assertEqual((m["station"], m["category"], m["clouds"], m["ceiling"], m["altim_inhg"]),
                         ("KORD", "VFR", "CLR", None, 30.15))
        cloudy = parse_metar([dict(METAR_JSON[0], clouds=[{"cover": "FEW", "base": 2500},
                                                          {"cover": "BKN", "base": 8000}])])
        self.assertEqual((cloudy["clouds"], cloudy["ceiling"]), ("FEW025 BKN080", 8000))
        self.assertIsNone(parse_metar([]))
        self.assertIsNone(parse_metar(None))
        self.assertTrue(parse_taf(TAF_JSON)["raw"].startswith("TAF KORD"))

    def test_ofp(self):
        p = parse_ofp(OFP_JSON)
        self.assertEqual((p["callsign"], p["origin"]["icao"], p["destination"]["rwy"], p["alternate"]["icao"]),
                         ("SWA2973", "KSLC", "30", "KSMF"))
        self.assertEqual((p["initial_alt"], p["ete"], p["units"], p["fuel"]["plan_ramp"], p["weights"]["max_tow"]),
                         (34000, 5787, "LBS", 12967, 160096))
        self.assertEqual(parse_ofp(dict(OFP_JSON, alternate=[{"icao_code": "KSMF"}]))["alternate"]["icao"], "KSMF")
        with self.assertRaises(ValueError):
            parse_ofp({"fetch": {"status": "Error: Unknown UserID"}})


class Phase3ServiceTests(unittest.TestCase):
    def test_weather_service_keeps_last_data_when_offline(self):
        now, calls = [1000.0], []

        def fetch(url, params):
            calls.append(url.rsplit("/", 1)[-1])
            if fail[0]:
                raise OSError("offline")
            return {"forecast": FORECAST_JSON, "metar": METAR_JSON, "taf": TAF_JSON}[url.rsplit("/", 1)[-1]]
        fail = [False]
        w = WeatherService(41.88, -87.63, "kord", fetch=fetch, wall_clock=lambda: now[0])
        w.poll()
        self.assertEqual(calls, ["forecast", "metar", "taf"])
        self.assertEqual(w.snapshot["forecast"]["temp"], 72.0)
        w.poll()
        self.assertEqual(len(calls), 3)                     # not due yet
        fail[0], now[0] = True, 1000.0 + 16 * 60
        w.poll()
        self.assertEqual(w.snapshot["forecast"]["temp"], 72.0)
        self.assertIn("offline", w.snapshot["error"])
        fail[0] = False
        w._set_airport("KSLC")
        w.poll()
        self.assertEqual(w.snapshot["airport"], "KSLC")

    def test_simbrief_service(self):
        seen = []

        def fetch(url, params, timeout):
            seen.append(params)
            return OFP_JSON
        sb = SimBriefService("123456", "pilot1", fetch=fetch, wall_clock=lambda: 50.0)
        sb.poll()
        self.assertEqual(seen, [{"json": 1, "userid": "123456"}])
        self.assertEqual(sb.snapshot["plan"]["callsign"], "SWA2973")
        sb.poll()
        self.assertEqual(len(seen), 1)
        sb._refresh()
        sb.poll()
        self.assertEqual(len(seen), 2)
        bad = SimBriefService("", "nobody", fetch=lambda *a, **k: {"fetch": {"status": "Error: Unknown UserID"}})
        bad.poll()
        self.assertEqual(bad.snapshot["error"], "ERROR: UNKNOWN USERID")
        self.assertFalse(SimBriefService().snapshot["configured"])


class FakePipe:
    """Plays Discord's side of the RPC pipe."""

    def __init__(self, up=True):
        self.up, self.inbox, self.sent = up, b"", []

    def connect(self):
        return self.up

    def push(self, op, payload):
        self.inbox += dsc.encode_frame(op, payload)

    def available(self):
        if not self.up:
            raise OSError("closed")
        return len(self.inbox)

    def read(self, n):
        data, self.inbox = self.inbox[:n], self.inbox[n:]
        return data

    def write(self, data):
        import json
        import struct
        op, length = struct.unpack("<II", data[:8])
        self.sent.append((op, json.loads(data[8:8 + length])))

    def close(self):
        self.up = False

    def last(self, cmd):
        return [m for op, m in self.sent if m.get("cmd") == cmd][-1]

    def reply(self, cmd, data, error=False):
        self.push(1, {"cmd": cmd, "data": data, "nonce": self.last(cmd)["nonce"], "evt": "ERROR" if error else None})


class Phase3DiscordTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.token_path = os.path.join(self.dir.name, "discord_token.json")
        self.pipe = FakePipe()
        self.posts = []
        self.now = [1000.0]

        def post(url, fields):
            self.posts.append(fields)
            return {"access_token": "AT", "refresh_token": "RT", "expires_in": 604800}
        self.d = dsc.DiscordService("123", "secret", light_minutes=1, transport_factory=lambda: self.pipe,
                                    token_path=self.token_path, post=post, clock=lambda: self.now[0],
                                    wall_clock=lambda: self.now[0])

    def tearDown(self):
        self.dir.cleanup()

    def connect_and_approve(self):
        self.d.poll()                                        # connect + handshake
        self.assertEqual(self.pipe.sent[0], (0, {"v": 1, "client_id": "123"}))
        self.pipe.push(1, {"cmd": "DISPATCH", "evt": "READY", "data": {}})
        self.d.poll()
        self.assertEqual(self.d.snapshot["state"], "APPROVE IN DISCORD")
        self.pipe.reply("AUTHORIZE", {"code": "C0DE"})
        self.d.poll()
        self.assertEqual(self.posts[0]["grant_type"], "authorization_code")
        self.assertEqual(self.posts[0]["redirect_uri"], "http://localhost")
        self.assertEqual(self.pipe.last("AUTHENTICATE")["args"], {"access_token": "AT"})
        self.pipe.reply("AUTHENTICATE", {"user": {"username": "pilot1", "global_name": None}})
        self.pipe.push(1, {"cmd": "GET_VOICE_SETTINGS", "data": {"mute": False, "deaf": False},
                           "nonce": "x"})                    # unknown nonce: ignored
        self.d.poll()

    def test_first_run_authorize_then_events(self):
        self.connect_and_approve()
        s = self.d.snapshot
        self.assertEqual((s["state"], s["user"]), ("CONNECTED", "pilot1"))
        subs = [m["evt"] for op, m in self.pipe.sent if m.get("cmd") == "SUBSCRIBE"]
        self.assertEqual(subs, ["VOICE_SETTINGS_UPDATE", "NOTIFICATION_CREATE", "VOICE_CHANNEL_SELECT"])
        self.pipe.reply("GET_SELECTED_VOICE_CHANNEL", {"name": "General", "voice_states": [
            {"nick": "", "user": {"username": "pal", "global_name": "Pal"}, "voice_state": {"self_mute": True}}]})
        self.pipe.push(1, {"cmd": "DISPATCH", "evt": "VOICE_SETTINGS_UPDATE", "data": {"mute": True, "deaf": False}})
        self.pipe.push(1, {"cmd": "DISPATCH", "evt": "NOTIFICATION_CREATE",
                           "data": {"title": "Pal (#general)", "body": "hi"}})
        self.pipe.push(3, {"ping": 1})
        self.d.poll()
        s = self.d.snapshot
        self.assertEqual(s["channel"], {"name": "General", "members": [{"name": "Pal", "mute": True, "deaf": False}]})
        self.assertEqual((s["mute"], s["unseen"], s["notifications"][0]["body"]), (True, 1, "hi"))
        self.assertEqual(self.pipe.sent[-1], (4, {"ping": 1}))
        self.now[0] += 61                                    # light_minutes=1: the MSG light times out
        self.d.poll()
        self.assertEqual(self.d.snapshot["unseen"], 0)
        self.d._when_connected(self.d._toggle, "mute")
        self.assertEqual(self.pipe.last("SET_VOICE_SETTINGS")["args"], {"mute": False})

    def test_saved_token_is_reused_and_refreshed(self):
        self.connect_and_approve()
        self.pipe = FakePipe()
        self.d._reconnect()
        self.d.poll()
        self.pipe.push(1, {"cmd": "DISPATCH", "evt": "READY", "data": {}})
        self.d.poll()
        self.assertEqual(self.pipe.last("AUTHENTICATE")["args"], {"access_token": "AT"})
        self.pipe.reply("AUTHENTICATE", {"code": 4009, "message": "Invalid token"}, error=True)
        self.d.poll()
        self.assertEqual(self.posts[-1]["grant_type"], "refresh_token")
        self.assertEqual(len([m for op, m in self.pipe.sent if m.get("cmd") == "AUTHENTICATE"]), 2)

    def run_queued(self):
        while not self.d._q.empty():                         # what the service thread would do
            fn, args = self.d._q.get_nowait()
            fn(*args)

    def test_queued_actions_run_on_the_real_service(self):
        self.connect_and_approve()
        self.pipe.push(1, {"cmd": "DISPATCH", "evt": "NOTIFICATION_CREATE", "data": {"title": "t", "body": "b"}})
        self.d.poll()
        self.assertEqual(self.d.snapshot["unseen"], 1)
        self.d.mark_seen()
        self.run_queued()
        self.assertEqual((self.d.snapshot["unseen"], len(self.d.snapshot["notifications"])), (0, 1))
        self.d.clear_notifications()
        self.d.toggle_mute()
        self.d.reconnect()
        self.run_queued()
        self.assertEqual(self.d.snapshot["notifications"], [])
        self.assertEqual(self.pipe.last("SET_VOICE_SETTINGS")["args"], {"mute": True})

    def test_denied_and_not_running(self):
        self.d.poll()
        self.pipe.push(1, {"cmd": "DISPATCH", "evt": "READY", "data": {}})
        self.d.poll()
        self.pipe.reply("AUTHORIZE", {"code": 5000, "message": "access_denied"}, error=True)
        self.d.poll()
        self.assertEqual(self.d.snapshot["state"], "NOT APPROVED")
        self.pipe.up = False
        self.d.poll()
        self.assertEqual(self.d.snapshot["state"], "DISCORD NOT RUNNING")
        self.assertFalse(dsc.DiscordService("", "").snapshot["configured"])


class Phase3PageTests(unittest.TestCase):
    def setUp(self):
        self.s, self.app, self.ctx, self.clock = make()

    def test_weather_pages(self):
        self.s.press("DES")
        t = self.s.text()
        for expected in ("72°F", "CLEAR", "SSW 12 G21 MPH", "75/50°", "NEXT HOURS", "68 64 61"):
            self.assertIn(expected, t)
        self.s.press("LSK6R")
        self.assertIn("LIGHT RAIN", self.s.text())
        self.assertIn("RAIN 80%", self.s.text())
        self.s.press("LSK6R")
        t = self.s.text()
        for expected in ("METAR", "<KORD", "VFR", "CALM", "10+ SM", "23/10°C", "30.15 IN", "1021 HPA"):
            self.assertIn(expected, t)
        self.s.press("K", "S", "L", "C", "LSK1L")
        self.assertEqual(self.ctx.weather.calls, [("set_airport", "KSLC")])
        self.s.press("LSK1L")
        self.assertEqual(self.ctx.weather.calls[-1], ("set_airport", None))
        self.s.press("LSK6R")
        rows = [self.s.row(r) for r in range(14)]
        for group in ("TAF KORD", "FM220600", "TEMPO 2210/2212"):   # each change group starts a line
            self.assertTrue(any(r.startswith(group) for r in rows), group)
        self.assertIn(" SKC", [r.rstrip() for r in rows])           # wrapped lines are indented

    def test_weather_offline_and_dashboard(self):
        self.ctx.weather.snapshot["error"] = "URLError: offline"
        self.s.press("DES")
        self.assertIn("OFFLINE -", self.s.row(11))
        self.s.press("INIT_REF")
        self.assertIn("72°F CLEAR", self.s.row(4))
        self.assertNotIn("VOL", self.s.text())
        self.s.press("LSK2R")
        self.assertIn("WEATHER", self.s.row(0))

    def test_simbrief_pages(self):
        self.s.press("MENU", "LSK5R")
        t = self.s.text()
        for expected in ("KSLC", "KOAK", "SWA2973", "B38M", "FL340", "557 NM 1:36", "KSMF", "AIRAC 2610"):
            self.assertIn(expected, t)
        self.s.press("NEXT_PAGE")
        self.assertIn("FUEL", self.s.row(0))
        self.assertIn("12,967", self.s.text())
        self.s.press("NEXT_PAGE")
        self.assertIn("146,415", self.s.text())
        self.s.press("NEXT_PAGE")
        self.assertIn("KSLC/34L DEZRT2 MVA DCT", self.s.text())
        self.s.press("NEXT_PAGE")
        self.assertIn("DEP KSLC - AT PLANNING", self.s.text())
        self.s.press("LSK6R")
        self.assertEqual(self.ctx.simbrief.calls, [("refresh",)])

    def test_simbrief_not_set_up(self):
        self.ctx.simbrief.snapshot = {"plan": None, "configured": False}
        self.s.press("MENU", "LSK5R")
        self.assertIn("NOT SET UP", self.s.text())

    def test_discord_page_controls_and_msg_light(self):
        self.ctx.discord.snapshot["unseen"] = 2
        self.app.refresh()
        self.assertTrue(self.s.lights["MSG"])
        self.s.press("MENU", "LSK3R")
        t = self.s.text()
        for expected in ("CONNECTED", "PILOT1", "<MUTE", "DEAFEN>", "GENERAL", "2 IN CALL", "FRIEND M", "2 NEW"):
            self.assertIn(expected, t)
        self.s.press("LSK2L", "LSK2R", "LSK3R")
        self.assertEqual(self.ctx.discord.calls, [("toggle_mute",), ("toggle_deaf",), ("leave_channel",)])
        self.s.press("LSK6R")
        self.assertIn("NOTIFICATIONS", self.s.row(0))
        self.assertIn("READY FOR THE FLIGHT?", self.s.text())
        self.assertIn(("mark_seen",), self.ctx.discord.calls)
        self.ctx.discord.snapshot["unseen"] = 0
        self.app.refresh()
        self.assertFalse(self.s.lights["MSG"])
        self.s.press("LSK6R")
        self.assertEqual(self.ctx.discord.calls[-1], ("clear_notifications",))

    def test_discord_states(self):
        self.ctx.discord.snapshot = {"configured": False, "state": "NOT SET UP"}
        self.s.press("MENU", "LSK3R")
        self.assertIn("NOT SET UP", self.s.text())
        self.ctx.discord.snapshot = dict(DISCORD, state="APPROVE IN DISCORD", channel=None)
        self.app.refresh()
        self.assertIn("PRESS AUTHORIZE", self.s.text())
        self.s.press("LSK5R")
        self.assertEqual(self.ctx.discord.calls, [("reconnect",)])


class Phase4IdleDimTests(unittest.TestCase):
    def setUp(self):
        self.s, self.app, self.ctx, self.clock = make()
        self.app.read_levels()                       # normal screen level 73 %
        self.pc_idle = [0.0]
        self.ctx.pc_idle = lambda: self.pc_idle[0]
        self.s.level_writes.clear()

    def go_idle(self, seconds=601):
        self.pc_idle[0] += seconds
        self.clock.t += seconds
        self.app.refresh()

    def test_dims_after_idle_and_fcd_key_wakes_without_acting(self):
        self.go_idle(599)
        self.assertEqual(self.s.level_writes, [])
        self.go_idle(2)
        self.assertEqual(self.s.level_writes, [("screen", 10)])
        self.assertTrue(self.app.dimmed)
        self.app.refresh()
        self.assertEqual(len(self.s.level_writes), 1)          # written once, not every refresh
        self.s.press("A")
        self.assertEqual(self.s.level_writes, [("screen", 10), ("screen", 73)])
        self.assertEqual(self.app.scratchpad, "")             # the waking press did nothing else
        self.s.press("A")
        self.assertEqual(self.app.scratchpad, "A")
        self.assertEqual(self.ctx.levels["screen"], 73)

    def test_pc_input_wakes_and_fcd_keys_count_as_activity(self):
        self.go_idle()
        self.pc_idle[0] = 0.5                                   # mouse moved
        self.app.refresh()
        self.assertFalse(self.app.dimmed)
        self.assertEqual(self.s.level_writes[-1], ("screen", 73))
        self.pc_idle[0] = 10_000                                # PC idle, but the FCD is being used
        self.clock.t += 500
        self.s.press("MENU")
        self.clock.t += 200
        self.app.refresh()
        self.assertFalse(self.app.dimmed)

    def test_never_brighter_disabled_and_alarm_wakes(self):
        self.ctx.levels["screen"] = 5                           # already dimmer than the idle level
        self.go_idle()
        self.assertEqual(self.s.level_writes, [])
        self.ctx.levels["screen"] = 73
        self.ctx.settings.set("idle_minutes", 0)
        self.go_idle()
        self.assertEqual(self.s.level_writes, [])
        self.ctx.settings.set("idle_minutes", 10)
        self.app.refresh()
        self.assertTrue(self.app.dimmed)
        self.ctx.timers.countdown.set(1)
        self.ctx.timers.countdown.start()
        self.clock.t += 2
        self.app.refresh()
        self.assertFalse(self.app.dimmed)
        self.app.wake()                                         # exit path: no extra write when awake
        self.assertEqual(self.s.level_writes, [("screen", 10), ("screen", 73)])

    def test_settings_cycle_idle_options(self):
        self.s.press("N1_LIMIT")
        self.assertIn("<10 MIN", self.s.text())
        self.s.press("LSK4L", "LSK4R")
        self.assertEqual((self.ctx.settings["idle_minutes"], self.ctx.settings["idle_screen"]), (15, 20))
        for _ in range(3):                                      # 30, 60, then wraps to NEVER
            self.s.press("LSK4L")
        self.assertEqual(self.ctx.settings["idle_minutes"], 0)
        self.assertIn("<NEVER", self.s.text())

    def test_settings_shows_ambient_light_when_sensor_on(self):
        self.s.press("N1_LIMIT")
        self.assertIn("TYPE VALUE", self.s.row(11))           # photoresistor off: no reading
        self.s.light_levels = {"LIGHT_SENSOR_AB": 21673}
        self.app.refresh()
        self.assertIn("AMBIENT LIGHT 33%", self.s.row(11))

    def test_auto_brightness_switch(self):
        self.s.press("N1_LIMIT")
        self.assertIn("<--", self.s.row(10))                    # never set from the app
        self.s.press("LSK5L")
        self.assertEqual((self.s.sensor_writes, self.ctx.settings["auto_brightness"]), ([True], True))
        self.assertEqual(self.s.row(13).strip(), "AUTO BRIGHTNESS ON")
        self.s.press("CLR", "LSK5L")
        self.assertEqual((self.s.sensor_writes, self.ctx.settings["auto_brightness"]), ([True, False], False))
        self.assertIn("<OFF", self.s.row(10))
        self.s.light_levels = {"LIGHT_SENSOR_AB": 21673}           # frozen reading while off: not shown
        self.app.refresh()
        self.assertNotIn("AMBIENT", self.s.row(11))

    def test_changing_readings_correct_an_off_label(self):
        self.s.light_levels = {"LIGHT_SENSOR_AB": 20000}
        self.app.refresh()
        self.s.press("N1_LIMIT", "LSK5L", "CLR", "LSK5L")             # ON, then OFF from the app
        self.assertIs(self.ctx.settings["auto_brightness"], False)
        self.s.light_levels["LIGHT_SENSOR_AB"] = 20100               # late reading right after OFF
        self.app.refresh()
        self.assertIs(self.ctx.settings["auto_brightness"], False)
        self.clock.t += 60                                           # frozen: stays OFF
        self.app.refresh()
        self.assertIs(self.ctx.settings["auto_brightness"], False)
        self.s.light_levels["LIGHT_SENSOR_AB"] = 22500               # switched on elsewhere (Cockpit)
        self.app.refresh()
        self.assertIs(self.ctx.settings["auto_brightness"], True)
        self.assertIn("<ON", self.s.row(10))
        self.assertEqual(self.s.sensor_writes, [True, False])        # corrected the label only


if __name__ == "__main__":
    unittest.main()
