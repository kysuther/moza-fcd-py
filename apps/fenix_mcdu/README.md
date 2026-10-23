# Fenix A320 MCDU for the MOZA FCD

Shows the Fenix A320's MCDU on the MB7F FCD and drives it from the FCD's keys. The FCD has a 737-style keypad, so the Airbus keys are mapped onto it (below). Nothing in the aircraft is changed.

```bash
cd moza_fcd_py
.venv\Scripts\python.exe -m moza_fcd run --app apps/fenix_mcdu/fenix_mcdu.py
```

**Requirements:** `pip install SimConnect "python-socketio[client]"` (both already in the project's `.venv`). The sim and the Fenix must be running.

**MOZA Cockpit:** only one program can drive the FCD. Close Cockpit (and MOZA Device Service), start this app, then start Cockpit again for your other MOZA hardware.

## How it works

- **Screen:** the Fenix's GraphQL gateway (`Fenix.GqlGateway`, port 8083) streams the MCDU screen.
  - Each update has 14 rows: title, 12 lines, scratchpad.
  - Inline codes switch small/large text and colour (amber, cyan, yellow, white, green, magenta).
  - Arrows, the box prompt and the overfly Δ are drawn as symbols.
- **Keys:** each FCD key press sets the aircraft's own MCDU key variable (`L:S_CDU1_KEY_…`) through SimConnect while the key is held. Every press lasts at least 0.1 s so the Fenix registers it.

The screen and keys use separate connections. If SimConnect isn't connected, the screen still updates but keys do nothing; the terminal log says so.

## Keys

| FCD key | A320 MCDU |
|---|---|
| INIT REF | INIT |
| RTE | F-PLN |
| LEGS | DIR |
| DEP ARR | AIRPORT |
| CLB | PERF |
| CRZ | FUEL PRED |
| PROG | PROG |
| N1 LIMIT | DATA |
| HOLD | RAD NAV |
| FIX | SEC F-PLN |
| DES | ATC COMM |
| MENU | MCDU MENU |
| PREV PAGE / NEXT PAGE | ← / → |
| BRT+ / BRT− | ↑ / ↓ |
| DEL | OVFY |
| CLR | CLR |
| `+/-` | − (minus) |
| EXEC | (not used) |

Letters, digits, `.`, `/`, SP and all 12 line keys match their labels. Because BRT+/BRT− are the arrows here, set the FCD's brightness before flying (or with Cockpit's slider).

## Settings

| Environment variable | Default | Meaning |
|---|---|---|
| `FENIX_MCDU` | `1` | `1` captain MCDU, `2` first officer's (screen and keys) |
| `FENIX_GQL` | `ws://localhost:8083/graphql/` | The Fenix GraphQL gateway |

## Not included

The Fenix MCDU's own annunciators (FAIL, FM, IND, RDY, MCDU MENU) aren't mapped to the FCD's lights yet.

Tests: `python -m unittest tests.test_fenix_mcdu`.
