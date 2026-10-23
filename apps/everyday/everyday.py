"""Everyday-use app for the MOZA MB7F FCD: menu, dashboard, audio, media, system, clock/timers, settings
(Phase 1), launcher, tools (calculator, converter, notes, clipboard) and Windows controls (Phase 2), and
weather/METAR, SimBrief and Discord (Phase 3).

    cd moza_fcd_py
    .venv\\Scripts\\python.exe -m moza_fcd run --app apps/everyday/everyday.py    # real FCD
    .venv\\Scripts\\python.exe -m moza_fcd gui --app apps/everyday/everyday.py    # emulator

Function keys: MENU menu, INIT REF dashboard, RTE audio, CLB media, CRZ system, HOLD clock/timers,
N1 LIMIT settings, LEGS launcher, DEP ARR Windows, PROG tools, FIX calculator, DES weather,
BRT -/+ screen brightness. Edit config.toml for clocks, alerts, mixer, launcher items and services.
"""

from __future__ import annotations

import threading

from moza_fcd import remap_color

from everyday_lib.app import Context, EverydayApp
from everyday_lib.config import START_PAGES, Settings, load_config
from everyday_lib.launcher import parse_items
from everyday_lib.pages.audio import AudioPage, MixerPage, OutputPage
from everyday_lib.pages.clock import ClockPage
from everyday_lib.pages.dashboard import DashboardPage
from everyday_lib.pages.discord import DiscordPage
from everyday_lib.pages.launcher import LauncherPage
from everyday_lib.pages.media import MediaPage
from everyday_lib.pages.menu import MenuPage
from everyday_lib.pages.settings import SettingsPage
from everyday_lib.pages.simbrief import SimBriefPage
from everyday_lib.pages.system import ProcessesPage, SystemPage
from everyday_lib.pages.tools import CalculatorPage, ClipboardPage, ConverterPage, NotesPage, ToolsPage
from everyday_lib.pages.weather import WeatherPage
from everyday_lib.pages.windows import WindowsPage
from everyday_lib.services.timers import Timers

FUNCTION_KEYS = {"MENU": "menu", "INIT_REF": "dashboard", "RTE": "audio", "CLB": "media", "CRZ": "system",
                 "HOLD": "clock", "N1_LIMIT": "settings", "LEGS": "launcher", "DEP_ARR": "windows",
                 "PROG": "tools", "FIX": "calculator", "DES": "weather"}


def build(screen, ctx: Context) -> EverydayApp:
    start = ctx.settings["start_page"]
    app = EverydayApp(screen, ctx, home=start if start in START_PAGES else "menu", function_keys=FUNCTION_KEYS)
    for name, page in (("menu", MenuPage()), ("dashboard", DashboardPage()), ("audio", AudioPage()),
                       ("mixer", MixerPage()), ("output", OutputPage()), ("media", MediaPage()),
                       ("system", SystemPage()), ("processes", ProcessesPage()), ("clock", ClockPage()),
                       ("settings", SettingsPage()),
                       ("launcher", LauncherPage(parse_items(ctx.config["launcher"]["items"]))),
                       ("windows", WindowsPage()), ("tools", ToolsPage()), ("calculator", CalculatorPage()),
                       ("converter", ConverterPage()), ("notes", NotesPage()), ("clipboard", ClipboardPage()),
                       ("weather", WeatherPage()), ("simbrief", SimBriefPage()), ("discord", DiscordPage())):
        app.add(name, page)
    return app


def make_context() -> Context:
    from everyday_lib import winapi
    from everyday_lib.notes import NotesStore
    from everyday_lib.services.audio import AudioService
    from everyday_lib.services.clipboard import ClipboardService
    from everyday_lib.services.discord import DiscordService
    from everyday_lib.services.media import MediaService
    from everyday_lib.services.simbrief import SimBriefService
    from everyday_lib.services.system import SystemService
    from everyday_lib.services.weather import WeatherService
    from everyday_lib.services.windows import WindowsService
    config = load_config()
    wx, sb, dc = config["weather"], config["simbrief"], config["discord"]
    return Context(
        config=config,
        settings=Settings(),
        audio=AudioService(hide=config["audio"]["mixer_hide"]).start(),
        media=MediaService().start(),
        system=SystemService(ping_host=config["system"]["ping_host"]).start(),
        timers=Timers(config["timers"]["pomodoro_focus_min"], config["timers"]["pomodoro_break_min"]),
        windows=WindowsService().start(),
        clipboard=ClipboardService().start(),
        notes=NotesStore(),
        pc_idle=winapi.idle_seconds,
        weather=WeatherService(wx["latitude"], wx["longitude"], wx["metar_airport"], wx["units"]).start(),
        simbrief=SimBriefService(sb["userid"], sb["username"]).start(),
        discord=DiscordService(dc["client_id"], dc["client_secret"], dc["redirect_uri"],
                               dc["notification_light_minutes"]).start(),
    )


def main(screen) -> None:
    ctx = make_context()
    try:
        remap_color("grey", ctx.config["display"]["grey"])
    except ValueError:
        remap_color("grey", "white")          # unknown colour name in config.toml
    app = build(screen, ctx)
    threading.Thread(target=app.read_levels, name="Everyday-levels", daemon=True).start()
    try:
        app.run()
    finally:
        for service in (ctx.audio, ctx.media, ctx.system, ctx.windows, ctx.clipboard, ctx.weather, ctx.simbrief,
                        ctx.discord):
            service.stop()
        try:
            app.wake()                    # never leave the screen at the idle level
            app.lights_off()
        except Exception:
            pass
