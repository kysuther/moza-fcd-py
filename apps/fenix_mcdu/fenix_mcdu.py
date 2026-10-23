"""Fenix A320 MCDU on the MOZA MB7F FCD (a 737-style keypad, so some keys are remapped - see KEY_MAP).

    python -m moza_fcd run --app apps/fenix_mcdu/fenix_mcdu.py     # real FCD (start before MOZA Cockpit)

Screen: the Fenix GraphQL gateway (Fenix.GqlGateway, ws://localhost:8083/graphql), subscription
`dataRefs(names: ["aircraft.mcdu<n>.display"])`. The value is XML: <title>, 12 <line>s, <scratchpad>; inside the
text, lowercase letters switch format (s small, l large, a/c/y/w/g/m colour) and £ ¢ ¤ ¥ # & stand for
← → ↑ ↓ ☐ Δ (format as in MobiFlight's fenix_winwing_cdu.py).
Keys: the aircraft's L-vars L:S_CDU<n>_KEY_<name> (1 = pressed, 0 = released), written through SimConnect
(names from the Fenix binaries; CLR/A tested live). Needs `pip install SimConnect "python-socketio[client]"`
(the latter brings websocket-client).

Environment variables:
  FENIX_MCDU = 1 (captain, default) | 2 (first officer)
  FENIX_GQL  = ws://localhost:8083/graphql/
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from moza_fcd import Page
from moza_fcd import keys as K

SIDE = 2 if os.environ.get("FENIX_MCDU", "1").strip() == "2" else 1
GQL_URL = os.environ.get("FENIX_GQL", "ws://localhost:8083/graphql/")
MIN_PRESS_S = 0.1          # hold each key at least this long so the aircraft sees it

# FCD key -> Fenix key name (L:S_CDU<n>_KEY_<name>). The 737 page keys take the A320's page keys.
KEY_MAP: Dict[str, str] = {
    "INIT_REF": "INIT", "RTE": "FPLN", "LEGS": "DIR", "DEP_ARR": "AIRPORT", "CLB": "PERF", "CRZ": "FUEL_PRED",
    "PROG": "PROG", "N1_LIMIT": "DATA", "HOLD": "RAD_NAV", "FIX": "SEC_FPLN", "DES": "ATC_COM", "MENU": "MENU",
    "PREV_PAGE": "ARROW_LEFT", "NEXT_PAGE": "ARROW_RIGHT", "BRT+": "ARROW_UP", "BRT-": "ARROW_DOWN",
    "DEL": "OVFLY", "CLR": "CLEAR", ".": "DOT", "/": "SLASH", "+/-": "MINUS", "SP": "SPACE",
}
KEY_MAP.update({c: c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"})
KEY_MAP.update({f"LSK{n}{s}": f"LSK{n}{s}" for n in range(1, 7) for s in "LR"})

COLORS = {"a": "amber", "c": "cyan", "y": "yellow", "w": "white", "g": "green", "m": "magenta"}
SYMBOLS = {"£": "←", "¢": "→", "¤": "↑", "¥": "↓", "#": "□", "&": "Δ"}

Cell = Tuple[str, str, bool]          # (char, colour name, small)


def log(message: str) -> None:
    print(f"[fenix] {time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


def lvar_for(key: str, side: int = SIDE) -> Optional[str]:
    name = KEY_MAP.get(key)
    return f"S_CDU{side}_KEY_{name}" if name else None


def parse_row(text: str) -> List[Cell]:
    """One display row: inline format letters change size/colour for the characters after them."""
    cells: List[Cell] = []
    small, color = False, "white"
    for ch in text or "":
        if ch == "s":
            small = True
        elif ch == "l":
            small = False
        elif ch in COLORS:
            color = COLORS[ch]
        else:
            cells.append((SYMBOLS.get(ch, ch), color, small))
    return cells[:24]


def parse_display(xml: str) -> List[List[Cell]]:
    """The MCDU XML -> 14 rows (title, 12 lines, scratchpad)."""
    root = ET.fromstring(xml)
    rows = [parse_row(child.text or "") for child in root][:14]
    return rows + [[] for _ in range(14 - len(rows))]


def render(rows: List[List[Cell]]) -> Page:
    pg = Page()
    for r, row in enumerate(rows[:14]):
        for c, (ch, color, small) in enumerate(row):
            if ch != " ":
                pg.write(r, c, ch, color, small=small)
    return pg


def waiting_page(line1: str, line2: str = "") -> Page:
    pg = Page().center(1, f"FENIX A320 MCDU {SIDE}", "cyan")
    pg.center(5, line1, "white")
    if line2:
        pg.center(7, line2, "white", small=True)
    pg.center(11, GQL_URL.replace("ws://", "").rstrip("/")[:24], "green", small=True)
    return pg


class DisplayLink:
    """GraphQL subscription to the MCDU display (graphql-transport-ws); reconnects by itself."""

    def __init__(self, url: str = GQL_URL, side: int = SIDE, connect=None):
        self.url, self.side = url, side
        self.connect = connect
        self.rows: Optional[List[List[Cell]]] = None
        self.connected = False
        self._stop = threading.Event()
        self._ws = None

    def start(self) -> "DisplayLink":
        threading.Thread(target=self._loop, name="Fenix-display", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    def _open(self):
        if self.connect:
            return self.connect()
        import websocket
        return websocket.create_connection(self.url, subprotocols=["graphql-transport-ws"], timeout=10)

    def _loop(self) -> None:
        last_error = None
        while not self._stop.is_set():
            try:
                self._ws = ws = self._open()
                ws.send(json.dumps({"type": "connection_init", "payload": {}}))
                query = "subscription($n:[String!]!){ dataRefs(names:$n){ name value } }"
                ws.send(json.dumps({"id": "1", "type": "subscribe", "payload": {
                    "query": query, "variables": {"n": [f"aircraft.mcdu{self.side}.display"]}}}))
                self.connected, last_error = True, None
                log("display connected")
                while not self._stop.is_set():
                    raw = ws.recv()
                    if not raw:
                        break
                    self.on_message(json.loads(raw), ws)
            except Exception as e:
                if str(e) != last_error:
                    last_error = str(e)
                    log(f"display link: {type(e).__name__}: {e}")
            self.connected = False
            self._stop.wait(3)

    def on_message(self, msg: dict, ws=None) -> None:
        kind = msg.get("type")
        if kind == "ping" and ws is not None:
            ws.send(json.dumps({"type": "pong"}))
        elif kind == "next":
            ref = ((msg.get("payload") or {}).get("data") or {}).get("dataRefs") or {}
            if isinstance(ref.get("value"), str):
                try:
                    self.rows = parse_display(ref["value"])
                except ET.ParseError:
                    pass


class KeyLink:
    """Writes the MCDU key L-vars through SimConnect on one thread (the SimConnect package isn't thread-safe)."""

    def __init__(self, writer_factory=None):
        self.writer_factory = writer_factory
        self.ready = False
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()

    def start(self) -> "KeyLink":
        threading.Thread(target=self._loop, name="Fenix-keys", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def write(self, lvar: str, value: int, not_before: float = 0.0) -> None:
        self._q.put((lvar, value, not_before))

    def _make_writer(self):
        if self.writer_factory:
            return self.writer_factory()
        from SimConnect import SimConnect
        from SimConnect.RequestList import Request
        sm = SimConnect()
        cache = {}

        def write(lvar, value):
            if lvar not in cache:
                cache[lvar] = Request((f"L:{lvar}".encode(), b"Number"), sm, _settable=True, _time=0)
            cache[lvar].value = value
        return write

    def _loop(self) -> None:
        writer, last_error = None, None
        while not self._stop.is_set():
            if writer is None:
                try:
                    writer, self.ready = self._make_writer(), True
                    log("SimConnect connected (keys)")
                except Exception as e:
                    if str(e) != last_error:
                        last_error = str(e)
                        log(f"SimConnect: {type(e).__name__}: {e}")
                    self._stop.wait(3)
                    continue
            try:
                lvar, value, not_before = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            delay = not_before - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            try:
                writer(lvar, value)
            except Exception as e:
                log(f"key write failed ({lvar}): {e}")
                writer, self.ready = None, False


class FenixMcdu:
    """Mirrors the Fenix MCDU onto the FCD and turns FCD key presses into MCDU key presses."""

    def __init__(self, screen, display: DisplayLink, keys: KeyLink, side: int = SIDE, clock=time.monotonic):
        self.screen, self.display, self.keys, self.side, self.clock = screen, display, keys, side, clock
        self._down: Dict[str, float] = {}
        screen.on_key = self.on_key

    def on_key(self, name: str, pressed: bool) -> None:
        lvar = lvar_for(name, self.side)
        if lvar is None:
            return
        if pressed:
            self._down[lvar] = self.clock()
            self.keys.write(lvar, 1)
        else:
            started = self._down.pop(lvar, None)
            not_before = (started + MIN_PRESS_S) if started is not None else 0.0
            self.keys.write(lvar, 0, not_before)

    def frame(self) -> Page:
        if not self.display.connected:
            return waiting_page("WAITING FOR FENIX", "START THE SIM + FENIX")
        if self.display.rows is None:
            return waiting_page("WAITING FOR MCDU DATA")
        return render(self.display.rows)

    def tick(self) -> None:
        self.screen.show(self.frame())


def main(screen) -> None:
    display = DisplayLink().start()
    keys = KeyLink().start()
    mcdu = FenixMcdu(screen, display, keys)
    log(f"MCDU {SIDE} ({'captain' if SIDE == 1 else 'first officer'})")
    try:
        while True:
            mcdu.tick()
            time.sleep(0.1)
    finally:
        display.stop()
        keys.stop()
