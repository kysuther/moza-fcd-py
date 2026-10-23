# MB7F FCD: hardware and protocol reference

Read this when a task touches the device itself: connecting, lights, settings, debugging bring-up, or extending the driver. For writing apps, SKILL.md is enough.

## Confidence levels

| Fact | Source | Status |
|---|---|---|
| Screen protocol (framing, handshake, streams, MCDU frames) | MobiFlight PR #3387 (MIT), written from MOZA's partner guide; **merged 2026-10-08**. MobiFlight's MOZA support is defined by `Joysticks/moza/moza_fcd_mcdu.joystick.json`, which lists only the MA3F (`0x1305`); an MB7F would need its own definition file. LEDs not supported there yet. It feeds MOZA FCDs from MobiFlight's per-aircraft CDU scripts (Fenix, iFly, PMDG...) | **Works on a real MB7F** (firmware 1.2.7.30) |
| MB7F = "T23 CDU", MA3F = "T23 MCDU" | MOZA Cockpit `bin/config/device_config.json`, firmware tables | Confirmed |
| Key map: HID buttons 1–71 | Cockpit's key-test QML, matched to the keypad layout | **Confirmed on a real MB7F** (10 keys spot-checked, all matched) |
| Light names EXEC, MSG, FAIL, CALL, OFST | Cockpit strings (`cdu_EXEC` …) | Confirmed |
| Light index numbering (`keys.LED_IDS`) | Scan on a real MB7F | **Confirmed**: 0 CALL, 1 FAIL, 2 EXEC, 3 MSG, 4 OFST; 5–20 nothing |
| Main-board command encodings | Command library embedded in `MOZA_SDK.dll`, layout cross-checked with Boxflat | `CD_LedEnable`, lighting reads and range writes confirmed on a real MB7F |
| Brightness | Watching MOZA Cockpit's sliders, then reading the device back | **Confirmed**: screen = screen-board setting `0x01` (%), keys = group 1 range (manual mode first), lamps = group 3 range. Group 2 ignores writes |
| Glyphs | Cockpit's font subsetting list | ASCII plus ← ↑ → ↓ ◀ ▶ ▲ ▼ ■ ◆ △ ▽ □ ◇ (box prompts available) |
| USB VID `0x346E` | All MOZA devices | Confirmed |
| PIDs | `0x1306` MB7F (real hardware, HID name `MOZA MB7F MFCD Display`); `0x1305` MA3F (MobiFlight) | Confirmed / reported |
| Windows HID reader | Tested live on a TWCS throttle | Works |
| SDK ctypes binding | Tested live on MOZA rudder pedals | Works |

## Device architecture

- **Two boards.**
  - The main board (`AS23-T23-HW_CDU-CU`) handles keys, lights, backlight and the light sensors.
  - The display board (`RS21-W17-HW_RGB-DU`) is the same hardware as MOZA's W17/W18/W20 steering-wheel displays. It renders the character grid with its own fonts and has its own built-in UI (clock, checklist, kneeboard). Its firmware is an AES-encrypted ZIP, so running custom code on it isn't realistic.
- **USB composite device:**
  - A **CDC serial port**: 2,000,000 baud, DTR on. It carries the screen and all main-board commands.
  - A **HID game controller** with 71 keys (the report declares 96 button usages), axes Rx/Ry/Z (the ambient-light sensors A, B and A+B), and reports at about 1 kHz. The sensors read 0 until the Photoresistor Switch is on in Cockpit (saved on the device); then the device auto-scales screen brightness from the configured level (confirmed).
  - Both use the same PID.
- **Display mode** is setting `0x18`: 0 is the device's own UI, 1 is the host-driven grid. The driver forces 0 then 1 on connect. With no host, the MB7F shows `HOMEPAGE / FIRMWARE VER / HOST NOT CONNECTED`, and it returns there shortly after the app exits.
- **Seat:** setting `0x13` (cabin position) is 0 for captain and 1 for first officer.

### Display board internals (from its own logs, relayed into Cockpit's logs)

MOZA Cockpit writes the display board's log lines to `%LOCALAPPDATA%\MOZA Firmware Manager\*.log` and `%LOCALAPPDATA%\MOZA Cockpit\logs\*.log` (search for `dashboard log`). They come from the screen-board serial device (device 19 on the same COM port; the main board is device 2).

- **Hardware and OS:** Allwinner R818 SoC, PowerVR GE8300 GPU (OpenGL ES 3.2), 640×480 panel, Linux on aarch64. The UI is a Qt app (`T23MainWindow`). Software V2.23.5.0, built 2026-07-07 (commit `ff5cf726`), on screen firmware 1.2.7.30.
- **Fonts:** the grid uses `_T23_FONT.ttf` (9,636 bytes, family "fonteditor") from `/home/moza/resource/fonts`. That folder also holds Roboto, Lato, Montserrat, Source Han Sans and others, which the wheel-dashboard engine uses.
- **Dashboard engine:** the board loads `/home/moza/resource/dashes/.T23 Display/.T23 Display.mzdash` (7 pages) for its screen test. That is the same `.mzdash` format MOZA's wheel displays use. Uploading custom dashboards is off-limits: the owner has ruled out anything that writes files or firmware to the device.
- **`displayMode` values (setting `0x18`):** 0 home, 1 host grid, 3 display test (Cockpit shows it while you change colours or offset), 4/5/6 the device's own tabs (4 is tab 1, 5 the checklist, 6 the scratchpad/notes). The device writes the new value back when the user changes tab on the device.
- **Keys on the device side:** `T23 CDU key telemetry: index, physicalKey, keyCode` and `T23KeyInputRouter` (tabs, checklist, scratchpad) run on the display board. That is how the built-in UI works without a host.
- **Settings the board asks for that don't exist:** `gridLinesVisible` and `showDebugOverlay` (logged as "unknown setting url"). These are debug features with no setting id yet.

### Main-board firmware updates

The firmware update in Cockpit (1.1.1.5 → 1.1.1.8, 96.5 KB) is downloaded into memory and flashed over the same COM port. The device reboots into `boot0` and briefly enumerates as a different COM port (COM10), then comes back.

## Wire format

```
SerialLink: 7E | Len | Cmd | DevicePair | Payload(<=64) | Checksum
            checksum = (0x0D + 0x7E + sum(Len..Payload)) & 0xFF; 0x7E in the body is doubled (counted twice)
            host->device pair 0x12, device->host 0x21; replies set Cmd bit 7

Screen (Cmd 0x43 tunnel -> inner 0x7C "Reliable Stream", a stop-and-wait mini-TCP):
  The device opens connections (SYN1) to service ports; the host answers SYN2.
  9010 telemetry  - echo the device's 4-byte token (package 0x06). The screen board subscribes (package 0x04:
                    index + URL) to gameData/isGameRunning and moza/as23/T23 CDU(${port_name})/button/0..95
  9020 settings   - FF|Size|CRC32|Id|Data; 0x01 screen brightness (u32 %), 0x02 time sync, 0x07 protocol version,
                    0x13 cabin position, 0x17 text colour slot (index i32 + ARGB),
                    0x18 display mode, 0x1B display offset (x,y i32)
  9030 files      - font upload (Cockpit subsets fonts with subset_font.exe); format unknown, not implemented
  9050 MCDU       - InitConfig 0x32 -> ClientCapability 0x33 -> Keyframe 0x30 / Delta 0x31
                    (text rows + style rows, style byte = bit7 large | colour code 1-8 = Cockpit text colours 1-8
                    (screen fw 1.2.7.32+: 8-15 = Text Color 9 grey; <= 1.2.7.30: 8 = black, 9-15 repeat 1-7; 0 draws nothing); zlib when smaller;
                     strictly increasing sequence numbers; keyframe resent every 750 ms)

Main board (plain frames, Cmd = group, payload = ids + args; from MOZA_SDK.dll commands_as23.json):
  CD_LedEnable          69 | 98, index(0-20), on      -> 7E 03 45 12 62 ii oo cs
  CD_LedTestAll         69 | 105, on
  telemetry brightness  69 | 83, group, 0-255   (no visible effect in manual mode)
  lighting mode         69 | 81, group, 0 manual / 1 telemetry / 2 device
  brightness range      69 | 82, group, 0-15   (group 1 keys, 3 lamps; set mode 69|81 group 0 first)
  reads                 68 | 81/82/83, group (+0 for group 1 range/bright) -> reply C4 [id, group, value]
  firmware log          Cmd 0E from the device: [level, ascii] e.g. 'param_manage.c:340 Table 2, Param 7 Written: 15'
  CD_CockPitSide        69 | 89, 0/1         CD_EnableLightSensor 69 | 88, 0/1
  CD_EnableNote         69 | 96, 0/1         CD_EnableChecklist   69 | 97, 0/1
```

The full command library is embedded as JSON in `MOZA_SDK/x64-release/bin/MOZA_SDK.dll`. Search for `{"commands":[`. ControlPanel entries have `"deviceType": 256`, and `requestCmdIds` is `[group, id…]`.

## Driver map (`moza_fcd/`)

| File | Role |
|---|---|
| `protocol.py` | Pure encoders/decoders, plus the `CMD_*` main-board constants and `encode_device_command` |
| `session.py` | Pure state machines driven by explicit `now`: `ScreenSession`, stream multiplexer, settings/telemetry/MCDU channels |
| `device.py` | `FcdScreen`: serial transport, pump thread, keyframe refresh, lights and settings helpers. Use `port="sim"` for the simulator |
| `sim.py` | `SimulatedFcd`: the device side of the protocol, used by tests and the GUI |
| `page.py` | `Page` / `Cell` |
| `app.py` | `CduApp` / `CduPage` framework |
| `keys.py` | Key names, `MB7F_HID_BUTTONS`, `LIGHTS`, `LED_IDS`, PC keyboard map |
| `hidwin.py` | ctypes `hid.dll` enumeration and reading (Windows' own HID parser) |
| `buttons.py` | `FcdKeyReader`: HID buttons and axes → key names and sensor values |
| `sdk.py` | ctypes binding to the official `MOZA_SDK.dll` (DeviceService route; locks the port; auto-starts `MOZADeviceService.exe`) |
| `gui.py` | tkinter emulator |
| `demo_app.py` | Reference app |

## Hardware bring-up checklist (for a new unit or model)

1. Close MOZA Cockpit, or start the app before it: whichever opens the serial port first keeps it. DeviceService skips a device whose port is already held and picks it up again when it's released.
2. Run `python -m moza_fcd list`. An FCD is a `346E:xxxx` entry with both a COM port and a HID game controller. The MB7F is `0x1306`.
3. Run `python -m moza_fcd demo -v` (add `--pid` for an unknown model). The trace shows how far bring-up got: root handshake, device init, settings, MCDU capability, display mode.
4. Run `python -m moza_fcd hid` and press keys to confirm the key map.
5. For a new model, run `python -m moza_fcd lights --scan`, then add its PID to `protocol.KNOWN_FCD_PRODUCT_IDS`.
