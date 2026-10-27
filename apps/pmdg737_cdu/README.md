# PMDG 737 CDU

The Moza Cockpit software supports the PMDG 737 out of the box. This app was added because when attempting to use it, the screen was not updating for me at the time. Try the official software before using this.

The CDU of the PMDG 737 (MSFS) on the MOZA MB7F FCD: screen, keys and lights, read straight from PMDG's SDK. It works with every 737 variant PMDG ships (they share the NG3 SDK). Use it instead of MOZA Cockpit's 737 support, not alongside it.

```bash
pip install SimConnect
python -m moza_fcd run --app apps/pmdg737_cdu/pmdg737_cdu.py
```

Start it before MOZA Cockpit (or close Cockpit first). The order with the sim doesn't matter.

## One-time setup in the 737

PMDG only shares the CDU when its options file says so. Open `737_Options.ini` in the aircraft's `work` folder:

- MSFS 2024: `%LOCALAPPDATA%\Packages\Microsoft.Limitless_8wekyb3d8bbwe\LocalState\WASM\MSFS2024\<pmdg-aircraft-73x>\work\`
- MSFS 2020: `%LOCALAPPDATA%\Packages\Microsoft.FlightSimulator_8wekyb3d8bbwe\LocalState\packages\<pmdg-aircraft-73x>\work\`

Add, or complete, this section, then reload the aircraft:

```ini
[SDK]
EnableDataBroadcast=1
EnableCDUBroadcast.0=1
EnableCDUBroadcast.1=1
```

`EnableCDUBroadcast.0` is the captain's CDU, `.1` the first officer's, and `EnableDataBroadcast` gives the lights. If the FCD keeps showing `WAITING FOR PMDG 737`, this section is missing or the aircraft wasn't reloaded.

## How it works

- **Screen.** PMDG's CDU client data area (`PMDG_NG3_CDU_0` or `_1`): 24×14 cells with colour and small-font flags. Inop entries show grey, and an unpowered CDU is blank.
- **Keys.** PMDG's `EVT_CDU_L_*` (or `EVT_CDU_R_*`) events: a left click on press and a left release on release, so holding CLR clears the whole scratchpad as in the aircraft.
- **Lights.** EXEC, CALL, FAIL, MSG and OFST from `PMDG_NG3_Data`.
- The app calls `SimConnect.dll` directly (from the `SimConnect` package), because the Python wrapper doesn't return client data.

Every FCD key matches the 737 keypad. BRT − / BRT + set the FCD's own screen brightness.

## Settings

`PMDG737_CDU` (environment variable): `0` for the captain's CDU (default), `1` for the first officer's.
