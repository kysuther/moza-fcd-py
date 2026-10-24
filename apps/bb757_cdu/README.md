# BlueBird 757 CDU

The captain's CDU of the BlueBird Simulations 757 (MSFS) on the MOZA MB7F FCD: screen, keys and lights.

```bash
pip install SimConnect websockets
python -m moza_fcd run --app apps/bb757_cdu/bb757_cdu.py
```

Start it before MOZA Cockpit (or close Cockpit first), and the order with the sim doesn't matter.

## How it works

- **Screen.** The 757 drives WinWing-style CDUs itself. Its code connects to `ws://localhost:8320/winwing/cdu-captain` (MobiFlight's CDU interface), retrying every 5 seconds, and pushes the whole screen as `{"Target":"Display","Data":[...]}` whenever it changes. This app runs that server. Only one program can use port 8320, so **don't run MobiFlight Connector or the WINGFLEX app at the same time**. If the port is taken the FCD shows `PORT 8320 IN USE`.
- **Keys.** Each key is a held L-var, `L:B757_CDU_1_<KEY>`: 1 while pressed, 0 on release (held at least 0.1 s), written through SimConnect.
- **Lights.** Read from `L:FMC_EXEC_ACTIVE_1` and `L:B757_CDU1_<MSG|FAIL|OFST|DSPY>_decal`. The overhead light test lights MSG, FAIL, OFST and CALL too.

## Keys

The FCD's keypad is a 737 CDU. Most keys match the 757 by name. The 757's newer CDU layout has four different function keys:

| FCD key | 757 key |
|---|---|
| CLB | VNAV |
| CRZ | FMC COMM |
| DES | ATC |
| N1 LIMIT | NAV RAD |
| BRT − / BRT + | the FCD's own screen brightness |

The 757's DSPY light shows on the FCD's CALL light. Only the captain's CDU is supported.

## Settings

`BB757_PORT` (environment variable): the WebSocket port, default `8320`.
