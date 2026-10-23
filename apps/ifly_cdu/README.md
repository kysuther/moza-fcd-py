# iFly 737 MAX 8 CDU for the MOZA FCD

Shows the iFly 737 MAX 8's **captain CDU** on the MB7F FCD, with working keys and lights. No changes to the aircraft or its settings.

```bash
cd moza_fcd_py
.venv\Scripts\python.exe -m moza_fcd run --app apps/ifly_cdu/ifly_cdu.py
```

**Requirements:** `pip install "python-socketio[client]"` (already in the project's `.venv`). The iFly's EFB app (`iFly-EFB.exe`) must be running; it starts with the aircraft.

**MOZA Cockpit:** only one program can drive the FCD. Close Cockpit (and MOZA Device Service), start this app, then start Cockpit again: it leaves the FCD alone while the app holds it, and your other MOZA hardware keeps working.

## How it works

The iFly EFB runs a local web server (port 8084) with a web CDU. This app uses the same live feed:

- **Screen:** the CDU's 14 × 24 characters, colours and small/large font, the same grid as the FCD.
- **Lights:** EXEC, MSG, FAIL, CALL and OFST map to the FCD's five lights.
- **Keys:** sent back to the CDU by name, spaced 40 ms apart as the web CDU does.

The EFB's CDU is always the captain's, so that's the one shown.

Until the EFB answers, the FCD shows **WAITING FOR IFLY EFB**, then **WAITING FOR AIRCRAFT** until the iFly is loaded. It reconnects by itself if the sim restarts.

## Keys

Every FCD key does what its label says, with these exceptions:

| FCD key | iFly 737 MAX |
|---|---|
| CLB | VNAV |
| CRZ | FMC COMM |
| DES | ATC |
| BRT − / + | The FCD's own screen brightness (not the sim CDU's) |

Holding CLR clears the whole scratchpad, as on the real CDU.

## Display notes

- Grey text (the iFly's dimmed/inactive items) needs FCD screen firmware 1.2.7.32 or newer. On older screen firmware it's invisible: set `IFLY_GREY=white` (or another colour name).
- The iFly's highlighted cells (grey on a dark background) appear as plain text; the FCD can't draw cell backgrounds.
- Box prompts, degree signs and arrows use the FCD font's own symbols.

## Settings

| Environment variable | Default | Meaning |
|---|---|---|
| `IFLY_EFB_URL` | `http://localhost:8084` | Where the iFly EFB server is (its port is in `Data\Tool\EFB\port.ini` in the iFly package) |
| `IFLY_GREY` | `grey` | Colour used for grey text (`white` on screen firmware older than 1.2.7.32) |

Tests: `python -m unittest tests.test_ifly_cdu`.
