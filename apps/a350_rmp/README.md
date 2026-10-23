# A350 Radio Management Panel for the MOZA FCD

An iniBuilds A350-style RMP (VHF, NAV and ATC/TCAS pages) on the MB7F FCD's screen and keys.

The aircraft mapping was built from iniBuilds' A350 L-var list (the "A350 LVARs" PDF, February 2025, from the iniBuilds forum). That PDF isn't included in this repo; "the PDF" below refers to it. Requires `pip install SimConnect` for the sim link.

```bash
cd moza_fcd_py
python -m moza_fcd gui --app apps/a350_rmp/a350_rmp.py     # emulator
python -m moza_fcd run --app apps/a350_rmp/a350_rmp.py     # real FCD
```

## Keys

The FCD's 737 function keys act as the RMP's shortcut keys:

| FCD key | RMP |
|---|---|
| INIT REF | VHF |
| DES | NAV |
| CRZ | SQWK / ATC |
| MENU | MENU (key legend, sim-link status) |
| RTE / CLB | HF / TEL (not available) |
| PREV / NEXT PAGE | Cycle VHF → NAV → ATC |

Entries work the CDU way: type into the scratchpad, then press the line's key. Bad entries show `INVALID ENTRY` and light MSG; CLR clears.

## Pages

**VHF**
- RSK 1–3 enter the standby frequency. Accepted formats: `118.7`, `1187`, `118700`, or `18.7`; 8.33 kHz channels such as `121.505` are allowed.
- LSK 1–3 swap active and standby.
- LSK 4 moves the transmit selection VHF1 → 2 → 3.
- RSK 5 enters the squawk; LSK 5 opens the ATC page.

**NAV**
- LSK 2 toggles AUTO (tuned by the FMS) and STBY RAD NAV.
- RSK 1–3 open the LS, VOR and ADF pages. Entries there are refused unless STBY RAD NAV is on (`SELECT STBY RAD NAV`).
- LS: LSK 1 sets the frequency, RSK 1 the course.
- VOR 1/2: LSK sets the frequency, RSK the course.
- ADF 1/2: LSK sets the frequency (190–1750 kHz).

**ATC / TCAS**

| Key | Function |
|---|---|
| LSK 1 | XPDR system 1/2 |
| RSK 1 | ATC MODE AUTO/STBY (read-only when linked to MSFS) |
| LSK 2 | ALT RPTG ON/OFF (read-only when linked to MSFS) |
| RSK 2 | TCAS TA/RA → TA ONLY → STBY (only with ATC MODE AUTO, as on the A350) |
| LSK 3 | IDENT (shows `TRANSMITTING IDENT` for 18 s) |
| RSK 3 | Traffic NORM/ABV/BLW (A350 pedestal values) |
| LSK 4 | Squawk (4 digits, 0–7) |

TCAS needs ATC MODE AUTO, and Mode C needs ALT RPTG ON, as on the aircraft. The page warns when either isn't met.

## MSFS link (live-tested on MSFS 2024)

The link needs `pip install SimConnect` and MSFS running. Environment variables:

| Variable | Values |
|---|---|
| `RMP_BACKEND` | `auto` (default: MSFS when available, else standalone), `msfs` (require it), `local` |
| `RMP_NUMBER` | Which aircraft RMP to drive: `1` captain (default), `2` first officer, `3` backup |
| `RMP_FREQ_INPUT` | `events` (default) or `keys` (press the aircraft's own RMP keys). Both live-tested |
| `RMP_KEYS_CONFIRM` | `1` adds a second RSK press after the digits in `keys` mode. Not needed: the 6th digit commits |
| `RMP_LS_NAV` | Sim NAV radio for the LS frequency (default `3`) |

If MSFS was requested but isn't connected, the FAIL light comes on and the MENU page says so.

**What goes where** (full detail in [rmp_msfs.py](rmp_msfs.py)):

| Panel item | Sim side | Source |
|---|---|---|
| VHF1–3 frequencies, swap | Standard COM events (`COM_STBY_RADIO_SET_HZ`, `..._SWAP`) | iniBuilds: VHF1/2/3 drive COM1/2/3 |
| Transmit selection | `L:INI_RMP<n>_TRANSMIT_CHANNEL`, plus `L:INI_RMP<n>_<ch>_RECEIVE_SEL` = 1 so you also hear that radio | iniBuilds LVar list; confirmed in the cockpit |
| TCAS mode | `L:INI_tcas_mode_pedestal`: 0 STBY, 2 TA/RA, 3 TA ONLY | Observed in the cockpit. The PDF's numbering is wrong |
| Traffic | `L:INI_tcas_mode` (1 NORM, 2 ABV, 3 BLW) | iniBuilds LVar list |
| XPDR system | `L:INI_XPDR_SYS` (1/2) | iniBuilds LVar list |
| LS frequency | `NAV3_RADIO_SET_HZ` | MSFS SDK |
| VOR / ADF frequency | Not settable: the FCD says `TUNE VOR/ADF IN COCKPIT` | Live test |
| Squawk, IDENT | `XPNDR_SET`, `XPNDR_IDENT_ON` | MSFS SDK |
| ATC MODE + ALT RPTG | Read from `TRANSPONDER STATE:1` (read-only) | MSFS SDK; no A350 LVar listed |

TCAS, traffic, XPDR system, transmit channel and the frequencies are also read back, so changes made in the virtual cockpit show up on the FCD.

**Key-press mode.** If the A350 overrides the standard COM events, set `RMP_FREQ_INPUT=keys`. The app then works the aircraft's real RMP through its key LVars (`RMP_CPT_VHF_KEY`, `RMP_CPT_RSK_n_KEY`, `RMP_CPT_NUM_d_KEY`, `RMP_CPT_LSK_n_KEY`), entering the 6 digits the way a pilot would. Live-tested: the 6th digit commits the entry, and the key LVars reset themselves.

**Live test results** (MSFS 2024 + iniBuilds A350, 2026-10-09). The aircraft was powered and on the ground. Tests ran first with the transponder in STBY and RMP NAV in AUTO, then with ATC MODE AUTO and STBY RAD NAV. Each write was read back after 2 s, then restored.

| Item | Result |
|---|---|
| All reads (COM/NAV/XPDR SimVars, A350 LVars) | Work. The app mirrors the aircraft's radios, squawk, ATC mode, TCAS and traffic |
| VHF standby (25 kHz and 8.33 kHz), swap | **Work.** 8.33 kHz reads back as channel names |
| LS frequency (NAV3) | **Works** |
| VOR / ADF frequency | Ignored, in both AUTO and STBY RAD NAV. The aircraft's own RMP greys out LS/VOR too |
| VOR / LS course | No effect via `VOR1/2_SET` or `NAV OBS`. Course is panel-only |
| `L:INI_XPDR_SYS` | Writes stick |
| `L:INI_RMP1_TRANSMIT_CHANNEL` | **Works.** 2 lights the VHF2 transmit (CALL) key. Without reception selected, the A350 shows `VHF2 RECEPTION NOT SELECTED`, so the app also sets `RECEIVE_SEL` |
| `L:INI_tcas_mode` (traffic) | Writes stick. Default 1 = NORM, so the PDF's numbering is right |
| `L:INI_tcas_mode_pedestal` (TCAS) | **Works, both ways.** Mapped by logging the LVar while each mode was selected in the cockpit: 0 STBY, 2 TA/RA, 3 TA ONLY. Setting TA/RA from the app showed up in the cockpit. TCAS modes only change with the transponder in AUTO; in STBY the aircraft forces 0 |
| Squawk (`XPNDR_SET`) | **Works** |
| ATC MODE / ALT RPTG | **Read-only.** MSFS refuses writes to `TRANSPONDER STATE`, there's no state-set event, and the LVar list has nothing for them. AUTO on the ground reads as state 5 (GND). When linked to MSFS, the FCD shows the aircraft's state and says `SET ATC MODE IN COCKPIT` |
| IDENT (`XPNDR_IDENT_ON`) | **Works with ATC MODE AUTO** |
| Key-press mode | **Works.** VHF, RSK *n*, then 6 digits set the standby frequency |

**Caveats:**
- The SimConnect package's built-in lists lack most radio events, and it reads the wrong index for indexed variables. The bridge builds its own events and requests to avoid both.

## Tests

`python -m unittest tests.test_a350_rmp` covers:
- entry parsing
- every page flow
- the calls the panel makes to the sim backend
- the MSFS mapping (events, LVars, key-press mode), against a fake SimConnect
