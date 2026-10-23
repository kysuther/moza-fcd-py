---
name: moza-fcd-app
description: Build, run and test apps for the MOZA MB7F FCD (737-style CDU flight computer display) using the moza_fcd Python driver in this repo - drawing on its 14x24 character screen, handling its keypad (LSKs, scratchpad, EXEC), status lights, and testing in the desktop emulator without hardware. Use whenever the user wants an app, page, tool or integration that runs on the FCD/CDU/MCDU screen, or asks about how the device can be controlled.
---

# Building apps for the MOZA MB7F FCD

The driver is the `moza_fcd` package at the root of this repo. Run every command from the repo root, or `pip install -e .`. The driver's only dependency is `pyserial` (the apps in `apps/` need `requirements-apps.txt`). Python 3.8+; keys and the emulator need Windows.

## What you can and can't do

The device is a **character terminal**: 14 rows × 24 columns, 8 colours, large or small text per cell, plus 71 keys and 5 status lights.

- **Not possible:** pixels, bitmaps, images, or running code on the device. Don't promise them.
- **Hardware status:** the screen protocol, key map, status lights and auto-detect are **confirmed on a real MB7F** (PID `0x1306`). Screen, key-backlight and annunciator brightness are confirmed too. See [reference.md](reference.md).

## The app contract

An app is a Python file with a `main(screen)` function. The same file runs in both environments:

```bash
python -m moza_fcd gui --app path/to/app.py    # desktop emulator, no hardware
python -m moza_fcd run --app path/to/app.py    # real device (screen + keys)
```

The launcher has already connected the screen and waited for it to be ready before calling `main`. Rules:
- **No work at import time.** Module-level code runs when the loader imports the file.
- **Don't call `wait_ready()` or `connect()` in `main`.**
- **Don't read `sys.argv`** in `main`.
- **`main` may block forever**, e.g. `app.run()`.
- **For standalone use**, put that path under `if __name__ == "__main__":`, opening its own `FcdScreen(port=...)`. See `examples/hello.py`.

## Default approach: the CduApp framework

Use `moza_fcd.app` unless the user needs something non-CDU-like. It handles the 737 CDU conventions:
- the screen layout
- the scratchpad, CLR (hold to clear all), and DEL → `DELETE`
- `PREV_PAGE`/`NEXT_PAGE` with a `1/2` page indicator
- function keys that jump to pages
- the `MOD` title and EXEC light while changes are pending
- the MSG light while a scratchpad message shows

```python
import time
from moza_fcd.app import CduApp, CduPage

class Home(CduPage):
    title = "MY APP"                     # row 0, centred ("MOD " prefix added while exec_pending)
    page_count = 1                       # >1 enables PREV/NEXT and "1/2"; read self.page_index

    def draw(self, app, pg):             # draw rows 1-12; row 13 is the scratchpad
        app.line(pg, 1, time.strftime("%H:%M:%S"), "SET>", left_label="TIME", color="green")
        app.line(pg, 6, "<BACK", "NEXT>")

    def on_lsk(self, app, side, n):      # side "L"/"R", n 1-6; return True if handled
        if (side, n) == ("R", 1):
            if not app.scratchpad:
                app.show_message("ENTER VALUE")       # shows in scratchpad, lights MSG
            else:
                value = app.take_scratchpad()         # returns and clears it
                app.set_exec_pending(True)            # "MOD" title + EXEC light
            return True
        return False

    def on_exec(self, app):              # EXEC pressed while exec_pending; the flag clears itself
        pass

def main(screen):
    app = CduApp(screen, home="home", function_keys={"MENU": "home", "INIT_REF": "home"}, refresh_s=0.5)
    app.add("home", Home())
    app.run()                            # shows home, redraws every refresh_s (for live data); blocks
```

### Framework API

**`CduPage` hooks** (override any):

| Hook | Called when |
|---|---|
| `draw(app, pg)` | Each redraw; draw rows 1–12 |
| `on_lsk(app, side, n) -> bool` | A line select key is pressed |
| `on_key(app, name) -> bool` | Any other key; runs before the app's defaults. Return False to let defaults run |
| `on_exec(app)` | EXEC is pressed while `exec_pending` |
| `on_enter(app)` | The page becomes current |

Class attributes: `title`, `page_count`. Instance attribute: `page_index`.

**`CduApp` methods and state:**

| Name | Purpose |
|---|---|
| `add(name, page)` | Register a page; chainable |
| `go(name)` | Switch to a page |
| `line(pg, n, left, right, left_label, right_label, color, left_color, right_color, label_color)` | Draw line *n* (1–6): small labels on row `2n-1`, large data on row `2n` |
| `scratchpad` | Current scratchpad text |
| `take_scratchpad()` | Return the scratchpad and clear it |
| `show_message(text)` | Show a message in the scratchpad |
| `message` | The message currently showing |
| `exec_pending` / `set_exec_pending(bool)` | Pending-changes state |
| `refresh()` | Redraw now |
| `run()` / `stop()` | Start / stop the redraw loop |
| `add_key_hook(fn)` | Observe every key event |

**Behaviour to know:**
- **Typed characters** go to the scratchpad automatically: A–Z, 0–9, `.`, `/`, SP, and `+/-` (types `-`; pressing it again toggles to `+`). The limit is 24 characters.
- **Unhandled LSKs** are silently ignored, as on a real CDU.
- **CDU look:**
  - amber `____` placeholders for required empty fields
  - white for modified-but-not-executed values
  - cyan or green for active values
  - grey for unavailable options
  - `<LABEL` prompts on the left and `LABEL>` on the right
  - `<INDEX` / `<RETURN` on LSK 6L

**References in the repo:**
- `moza_fcd/demo_app.py`: a complete reference covering validation, MOD/EXEC/ERASE, paging and a notepad.
- `examples/my_app.py`: a minimal template.

## Without the framework

```python
from moza_fcd import Page
def main(screen):
    screen.on_key = lambda name, pressed: ...        # key names below; runs on the input thread
    pg = Page().center(0, "TITLE", "cyan").write(2, 0, "TEXT", "green").right(2, "1/2", small=True)
    screen.show(pg)
```

**`Page` methods:**
- `write(row, col, text, color="white", small=False)`
- `center(...)`, `right(...)`, `fill(row, char)`
- `lines(list, start_row)`
- `clear()`, `copy()`, `to_text()`
- `Page.from_winwing_json(...)` (WinWing/MobiFlight CDU JSON)

All writers return the page, so calls chain. Text past column 23 is clipped silently.

**`screen.show(page)`** is cheap and thread-safe. The first call sends a keyframe, later calls send only changed rows, and an unchanged page sends nothing. Calling it a few times a second is fine.

## Screen facts

- **Size:** rows 0–13, columns 0–23. Row 13 is the scratchpad by convention.
- **Colours:** names `cyan red yellow green magenta amber white grey`. Also accepted: WinWing letters `c r y g m a w e`, or raw codes 1–8. The user can remap the 8 colour slots and the background on the device, so don't rely on exact RGB.
  - **Grey needs screen firmware 1.2.7.32+.** There, codes 8–15 draw Cockpit's Text Color 9 (grey). On older screen firmware code 8 is Text Color 8 (black by design) and grey text is invisible. Give apps a setting that calls `moza_fcd.remap_color("grey", "white")` for old firmware (the everyday app's `[display] grey`, `IFLY_GREY`, `A220_GREY`), and don't ask the user to change Text Color 8.
- **Text size:** `small=True` gives the small font. Use small text for labels and large for data, as on a real CDU.
- **Characters:** stick to printable ASCII plus `°`. The device's font also has the arrows and shapes `← ↑ → ↓ ◀ ▶ ▲ ▼ ■ ◆ △ ▽ □ ◇` (from Cockpit's font subset list; not yet rendered on hardware). Avoid box-drawing and emoji. Control characters (NUL, tab, newline) are written as blanks by `Page.write`: a NUL reaching the device stops that row from updating (seen with the iFly's NUL-padded scratchpad). Use `_` or `-` for placeholders.
- **No cursor or blinking.** Fake blinking by toggling the text across redraws.

## Keys

Names come from `moza_fcd/keys.py`. Key events arrive as `on_key(name, pressed)`, both press and release. CduApp acts on presses, and on CLR release for hold-to-clear.

| Group | Names |
|---|---|
| Line select keys | `LSK1L`–`LSK6L`, `LSK1R`–`LSK6R` |
| Function keys | `INIT_REF RTE CLB CRZ DES MENU LEGS DEP_ARR HOLD PROG N1_LIMIT FIX PREV_PAGE NEXT_PAGE EXEC BRT- BRT+` |
| Characters | `A`–`Z`, `0`–`9`, `.`, `/`, `+/-`, `SP`, `DEL`, `CLR` |
| Unmapped hardware buttons | `BTN<n>` |

Helpers: `keys.parse_lsk(name) -> ("L", 3)`, `keys.typed_char(name)`.

**Threading:** handlers run on the input thread (the Tk thread in the emulator, a HID thread on hardware). Keep them fast. Put slow work (network, file I/O) on your own thread, then call `screen.show()` or `app.refresh()` from it. CduApp guards its state with a lock.

## Lights and device settings

- **Status lights:** `screen.set_light(name, on)` for `EXEC MSG FAIL CALL OFST`. It returns False before the session is running. CduApp already drives EXEC and MSG; manage CALL, FAIL and OFST yourself.
  - The light numbering in `keys.LED_IDS` is confirmed on hardware (CALL 0, FAIL 1, EXEC 2, MSG 3, OFST 4). Use names, never raw indices, in apps.
- **Other settings:**
  - **Brightness** (persists on the device; read it back first instead of assuming a level):
    - `set_screen_brightness(0-100)` / `screen_brightness()`
    - `set_key_backlight(0-15)` / `key_backlight()`
    - `set_indicator_brightness(0-15)` / `indicator_brightness()`
    - The getters for keys/lamps block briefly (`query_device`), so call them off the key thread.
    - `set_brightness()` is the telemetry-mode value and does nothing visible in manual mode.
  - `set_lighting_mode(mode, group)`
  - `set_cockpit_side(0|1)`
  - `enable_light_sensor(bool)`: the device's auto-brightness (Cockpit's Photoresistor Switch). Confirmed on hardware, saved on the device, write-only: track the state you set yourself. While off, sensor readings freeze rather than drop to 0.
  - `screen.set_display_mode(0)` hands the screen back to the device's own UI. Do that on exit if the user wants.
- **Light sensors** (hardware only): `buttons.FcdKeyReader(on_key, on_axis=...)` reports `LIGHT_SENSOR_A/B/AB` (0–65535); under `python -m moza_fcd run`, read `screen.light_levels` or set `screen.on_light`. They read 0 unless the device's **Photoresistor Switch** is on (MOZA Cockpit > FCD > Basic Settings; saved on the device). With it on, the device itself adjusts screen brightness: it scales the configured screen level by the ambient light, so `set_screen_brightness()` still works as the base level (confirmed on an MB7F). Key backlight and annunciators aren't affected. So don't build sensor-driven screen brightness in an app; the device already does it.

## Testing (do this before claiming an app works)

1. **Headless unit tests.** Drive the app with a fake screen and assert on the rendered rows. Copy the `FakeScreen` pattern from `tests/test_app.py`:
   ```python
   s = FakeScreen(); app = build_or_construct_app(s); app.go("home")
   s.press("A", "B", "LSK1L"); assert s.page.row_text(2).startswith("AB")
   ```
2. **Full test suite:** run `python -m unittest discover -s tests`. All tests must still pass.
3. **Emulator:** run `python -m moza_fcd gui --app your_app.py`. The window runs the real protocol against a simulated device.
   - PC keyboard: F1–F6 / F7–F12 are the LSKs, Enter is EXEC, Esc is CLR, PgUp/PgDn change page.
   - The status bar shows the last key and frame count.
4. **Terminal render:** with `FcdScreen(port="sim")`, `screen.simulator.render_ansi()` or `.screen_text()` shows what the device received.

## Integrating data

The driver ships no sim connectors. For flight-sim data, add a client library yourself:
- **MSFS:** SimConnect (e.g. `Python-SimConnect`)
- **X-Plane:** UDP datarefs, or the X-Plane web API

Then render the data into pages. Poll on your own thread and let `CduApp.refresh_s` or `screen.show()` push updates.

To mirror the iFly 737 MAX CDU: see `apps/ifly_cdu/`. It reads the iFly EFB's Socket.IO feed on port 8084 (`ifly_data_update` -> `gau.LSKChar` / `LSK_Color` / `LSK_SmallFont`, 2 CDUs x 14 x 24) and sends `send_cdu_key {id, value1, value2}`.

To mirror the Fenix A320 MCDU: see `apps/fenix_mcdu/`. GraphQL subscription `dataRefs(names:["aircraft.mcdu1.display"])` on `ws://localhost:8083/graphql/` (protocol graphql-transport-ws) returns XML rows with inline format letters; keys are the L-vars `S_CDU1_KEY_<name>` (1 pressed, 0 released) written via SimConnect.

To mirror the PMDG 737 CDU itself: PMDG publishes 14×24 CDU cells via SimConnect once `EnableCDUBroadcast` is set in `737_Options.ini`. Map them into a `Page` cell by cell.

## Safety rules

- **Close MOZA Cockpit before running on hardware.** It (via DeviceService) holds the serial port, and only one owner can drive the device.
- **Never point the driver at other MOZA devices.** Wheelbases and pedals share vendor ID `0x346E`. Auto-detect only matches known FCD PIDs on purpose. When the PID is unknown, ask the user to run `python -m moza_fcd list` and pass `--pid`/`--port`.
- **Never flash firmware or touch the bootloader, drivers or the screen board's files** (no firmware updates, no `boot0`, no 9030 uploads, no `.mzdash` dashboards). The owner doesn't want to risk bricking the device. Use only the features confirmed on hardware.
- **Don't experiment with unknown settings or commands** (`request_setting`, `send_device_command`, the 9030 file channel) unless the user asks. Tell them it's unverified.
