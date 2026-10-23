# Everyday app for the MOZA FCD

Everyday PC controls and information on the MB7F FCD.

- **Phase 1:** menu, dashboard, audio, media, system, clock/timers and settings.
- **Phase 2:** launcher, tools (calculator, unit converter, notes, clipboard) and Windows controls.
- **Phase 3:** weather and METAR/TAF, SimBrief flight plan, Discord voice and notifications.

Everything acts on the PC or online services. The FCD is only used as a screen, keypad and lights.

```bash
cd moza_fcd_py
.venv\Scripts\python.exe -m moza_fcd run --app apps/everyday/everyday.py    # real FCD
.venv\Scripts\python.exe -m moza_fcd gui --app apps/everyday/everyday.py    # emulator
```

**Setup:** copy `config.example.toml` to `config.toml` and edit it: your weather location, and optionally SimBrief and Discord. `config.toml` is git-ignored, so your secrets stay local. Without it, the app runs on built-in defaults.

**Requirements:** `pip install pyserial psutil pycaw comtypes tzdata` (or `pip install -r requirements-apps.txt` from `moza_fcd_py`). Windows PowerShell 5.1 (built into Windows) provides "now playing" info, and `nvidia-smi` (installed with the NVIDIA driver) provides GPU stats. If any of these is missing, that part shows `N/A` and the rest keeps working.

**MOZA Cockpit:** start this app *before* Cockpit, so it owns the FCD. If Cockpit is already running, the app can't open the port ("Access is denied").

## Keys

| Key | Page |
|---|---|
| MENU | Menu (the home screen; dashboard first) |
| INIT REF | Dashboard |
| RTE | Audio |
| CLB | Media |
| CRZ | System |
| HOLD | Clock / timers |
| N1 LIMIT | Settings |
| LEGS | Launcher |
| DEP ARR | Windows controls |
| PROG | Tools |
| FIX | Calculator |
| DES | Weather |
| BRT − / + | Screen brightness, in 10% steps |
| PREV / NEXT | Sub-pages (system disks, mixer apps, timers, launcher pages, power plans, clipboard history) |

Typing a number and then pressing a line key enters it, as on a real CDU: volume %, timer minutes (`25`, `1:30` or `90S`), brightness, calculations, notes.

## Pages

- **Dashboard:** local time and date, UTC time and weather, now playing, CPU/RAM/GPU, network, timer. Line keys jump to the full pages (RSK 2 opens Weather).
- **Audio:**
  - master volume (type % + LSK 1L), mute (RSK 1), ±step (LSK/RSK 2), mic mute (RSK 3)
  - **Mixer:** one line per app; type % + its LSK sets the volume, LSK alone mutes it
  - **Output:** LSK makes that device the default
- **Media:** title (wrapped over two lines), artist and position. PREV/NEXT on line 5; play/pause on RSK 6 or EXEC. Works with anything in Windows' media overlay (Spotify, browsers and so on).
- **System:**
  - CPU, RAM, GPU %, GPU temperature, VRAM, ping, network, uptime; NEXT PAGE for disks
  - **Processes:** top 5 by CPU; LSK selects one, EXEC ends it, `<CANCEL` backs out
- **Clock:**
  - local time plus up to 4 world clocks (from `config.toml`)
  - **Timers** (TIMERS> or NEXT PAGE): countdown, stopwatch, Pomodoro (RSK 6 skips the phase)
- **Launcher:** up to 10 items per page from `config.toml` (left column first). An item opens an app, file, folder or URL, sends a shortcut or a list of shortcut steps to the focused window, or types text. Items with `confirm = true` wait for EXEC. The small label shows the kind (OPEN, KEYS, TYPE), or the problem if the entry is invalid (BAD KEYS, NO ACTION). If an app fails to open, OPEN FAILED shows for a few seconds.
- **Windows:**
  - Lock, monitors off (move the mouse to wake them), keep awake on/off, show desktop, snip, dark/light mode.
  - Sleep, restart and shut down need EXEC. Restart and shut down wait `windows.shutdown_delay_s` (60 s) and show a countdown; ABORT> (RSK 5) cancels.
  - **Power plans** (NEXT PAGE or POWER PLANS>): LSK switches plan.
- **Tools:**
  - **Calculator:** type a calculation (`X` = times; the +/- key types `-`, press again for `+`), then CALC> or EXEC. Starting with an operator continues from the last answer (`+5`, `X2`). RSK 1–4: square root, square, 1/x, percent. LSK 1–4 put a previous result back in the scratchpad. COPY> puts the answer on the PC clipboard.
  - **Converter:** °F/°C, lb/kg, mi/km, ft/m, in/cm, gal/L, kt/km/h, kt/mph, NM/km, inHg/hPa. Type a value and press the LSK of the unit you're converting *from*. With an empty scratchpad it converts the last result again.
  - **Notes:** one scratchpad line each, up to 25, saved in `notes.json`. Type + LSK adds or replaces; LSK alone copies a note to the scratchpad to edit; DEL then LSK deletes; RSK copies it to the PC clipboard.
  - **Clipboard:** the clipboard's text, CLEAR, and SET> (scratchpad to clipboard). HISTORY> lists the last few text clips; LSK puts one back. The history is kept in memory only.
- **Weather** (the city and airport from `config.toml`; R6 steps through the pages):
  - **Now:** temperature, conditions, feels like, humidity, wind and gusts, high/low, rain chance, sunset, next hours. LSK 1L refreshes.
  - **Forecast:** today and the next 3 days.
  - **METAR:** flight category (VFR green, MVFR cyan, IFR red, LIFR magenta), age, wind, visibility, clouds, temperature/dew point, altimeter, and the raw report. Type an ICAO code + LSK 1L to look at another airport; LSK 1L alone goes back to your home airport.
  - **TAF:** one line per change group.
  - Forecast from Open-Meteo every 15 minutes, METAR/TAF from aviationweather.gov every 10. No accounts needed. Offline, the last data stays with its age.
  - The dashboard shows the temperature and conditions on line 2, under the date (RSK 2 opens Weather).
- **SimBrief:** your latest flight plan, fetched at startup, every 30 minutes and on REFRESH>. NEXT/PREV PAGE: summary (airports, runways, callsign, aircraft, initial altitude, cost index, block times, distance/ETE, alternate), fuel, weights (amber if over a limit), route, and the departure/arrival METARs from planning time.
- **Discord** (needs setup, see below):
  - Connection state, mute/deafen (LSK/RSK 2), your voice channel and who's in it (M = muted, D = deafened), LEAVE> to hang up.
  - **Notifications** (R6 or NEXT PAGE): the last few, newest first. A new one lights MSG until you open this page, or for `notification_light_minutes` (10).
- **Settings:** screen brightness (%), key backlight and annunciators (0–15); each line has `<DIM` / `BRIGHT>`, or type a value then `<DIM`. Page 1 also has idle dimming (LSK 4L: after 2–60 minutes or never; RSK 4: dim to 0–30 %) and **AUTO BRIGHTNESS** (LSK 5L: the device's photoresistor switch, ON/OFF). The bottom line shows the ambient light level while it's on. Page 2: the start page (LSK 1L, any main page) and a light test (RSK 1). The device can't report the photoresistor switch, so the app shows the state it last set. One exception: the light readings freeze while the switch is off, so if they change while the label says OFF (e.g. you turned it on in Cockpit), the label corrects itself to ON. Turning it off in Cockpit can't be detected. Brightness is saved on the device itself, the same values MOZA Cockpit's sliders change, and the app reads them at startup.
- **Idle dimming** (default: 10 minutes, to 10 %): with no keyboard/mouse input on the PC and no FCD key presses for that long, the screen dims. Moving the mouse, typing, or pressing any FCD key brings it back; an FCD key press that wakes the screen does nothing else. Only screen brightness changes (once to dim, once to wake), and it's restored when the app exits. A timer alarm also wakes it.

## Status lights

| Light | Meaning |
|---|---|
| CALL | Timer or Pomodoro finished. Flashes until you press any key, and that press does nothing else |
| FAIL | Microphone muted (`audio.mic_mute_light` in config) |
| MSG | An alert: sustained high CPU, RAM, GPU temperature or low disk (thresholds in `config.toml`), a scratchpad message, or a new Discord notification |
| EXEC | A confirmation is waiting (end process, sleep, restart, shut down, launcher items with `confirm`) |
| OFST | Reserved (Do Not Disturb, later phase) |

## Grey text

Labels, hints and unavailable options are drawn in grey, which needs FCD **screen firmware 1.2.7.32 or newer** (it added Cockpit's Text Color 9, "disabled or unavailable options"). On older screen firmware, grey shows as black (invisible): set `[display] grey = "white"` (or another colour) in `config.toml`, or update the screen firmware in MOZA Cockpit.

## Discord setup

The app talks to the Discord desktop app on your PC through Discord's local control interface (RPC), using an application you own. Only you (the app's owner) can use it this way.

1. Go to <https://discord.com/developers/applications> and choose **New Application** (any name, e.g. "FCD").
2. Open **OAuth2**. Copy the **Client ID**, then **Reset Secret** and copy the **Client Secret**.
3. Under **Redirects**, add `http://localhost` and save.
4. Put both values in `config.toml` under `[discord]` (`client_id`, `client_secret`) and restart the app.
5. With the Discord desktop app open, go to MENU > DISCORD. Discord shows an authorization prompt; choose **Authorize**.

After that it connects by itself whenever Discord is running. The tokens are stored in `discord_token.json` next to `config.toml`. Keep that file and your client secret private; delete the file to approve again. If approval fails or Discord was closed, RECONNECT> (RSK 5) tries again.

Notifications come from Discord's desktop-notification feed, so they follow your Discord notification settings (for example, none for muted channels). Discord doesn't share unread counts.

## Files

| File | What it holds |
|---|---|
| `config.example.toml` | The template to copy to `config.toml` (the only config file in git) |
| `config.toml` | Your settings (git-ignored), hand-edited: world clocks, Pomodoro lengths, ping host, alert thresholds, mixer filter, launcher items, shutdown delay, weather location and units, SimBrief user, Discord app |
| `settings.json` | Written by the app when you change the start page or idle dimming on the device |
| `notes.json` | Notes typed on the device |
| `discord_token.json` | Discord access tokens (private; delete to re-approve) |
| `everyday_lib/services/` | Background services, one thread each: audio (pycaw), media (PowerShell helper + media keys), system (psutil + nvidia-smi + ping), timers, windows (controls + launcher actions), clipboard, weather, simbrief, discord |
| `everyday_lib/winapi.py` | Windows calls (ctypes, `powercfg`, `shutdown`): keystrokes, clipboard, session/power, theme |
| `everyday_lib/calc.py`, `notes.py`, `launcher.py` | Calculator and conversions, the notes store, launcher config parsing |
| `everyday_lib/pages/` | One module per page. Add a page by adding a module and registering it in `everyday.py` |

Tests: `python -m unittest tests.test_everyday`.

## Phases

1. **Done:** menu, dashboard, audio, media, system, clock/timers, settings, lights.
2. **Done:** launcher/macros, calculator, converter, notes, clipboard, Windows controls.
3. **Done:** weather and METAR/TAF, SimBrief, Discord (voice state and controls, notifications on the MSG light). Discord needs your app details to try on hardware.
4. **Done:** idle dimming, the ambient-light reading, and the AUTO BRIGHTNESS switch on Settings. Automatic brightness is the device's own (MOZA Cockpit calls it the **Photoresistor Switch**). It scales screen brightness from the level you set (BRT keys, Settings), so the app's controls and idle dimming keep working with it.
