"""PMDG 737 (MSFS) CDU on the MOZA MB7F FCD: screen, keys and lights, straight from PMDG's SDK.

    python -m moza_fcd run --app apps/pmdg737_cdu/pmdg737_cdu.py     # real FCD (start before MOZA Cockpit)

Screen: PMDG's CDU broadcast, client data area PMDG_NG3_CDU_0 (captain) or _1 (F/O): 24x14 cells of
(symbol, colour, flags), column by column, plus a powered flag. Needs the [SDK] section in 737_Options.ini
(EnableCDUBroadcast.0=1 / .1=1, and EnableDataBroadcast=1 for the lights).
Keys: PMDG's EVT_CDU_L_* / EVT_CDU_R_* events, a left click on press and a left release on release.
Lights: CDU_annunEXEC/CALL/FAIL/MSG/OFST from the PMDG_NG3_Data area.
Talks to SimConnect.dll directly (ctypes) because Python-SimConnect doesn't hand client data back;
the DLL comes from `pip install SimConnect`. Works for every 737 variant PMDG ships (NG3 SDK).

Keys: as labelled. BRT -/+ set the FCD's own brightness.
Environment: PMDG737_CDU = 0 captain (default) / 1 first officer.
"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import time
from ctypes import POINTER, byref, c_char_p, c_float, c_void_p, c_uint32
from typing import Dict, Optional

from moza_fcd import Page
from moza_fcd.page import COLUMNS, ROWS

CDU = 1 if os.environ.get("PMDG737_CDU", "0").strip() == "1" else 0
MIN_PRESS_S = 0.1
SCREEN_STEP = 10

# From PMDG_NG3_SDK.h
CDU_AREA = (b"PMDG_NG3_CDU_0", b"PMDG_NG3_CDU_1")
CDU_AREA_ID = (0x4E473335, 0x4E473336)
CDU_DEFINITION = (0x4E473338, 0x4E473339)
DATA_AREA, DATA_AREA_ID, DATA_DEFINITION = b"PMDG_NG3_Data", 0x4E473331, 0x4E473332
SCREEN_BYTES = COLUMNS * ROWS * 3 + 1             # Cells[24][14] of (Symbol, Color, Flags) + Powered
ANNUN_OFFSET = 543                                # CDU_annunEXEC[2], CALL[2], FAIL[2], MSG[2], OFST[2]
ANNUN_ORDER = ("EXEC", "CALL", "FAIL", "MSG", "OFST")
FLAG_SMALL, FLAG_UNUSED = 0x01, 0x04
COLORS = ("white", "cyan", "green", "magenta", "amber", "red")
SYMBOLS = {0xA1: "←", 0xA2: "→", 0xA3: "↑", 0xA4: "↓", 0xB0: "°", 0xEA: "□"}
MOUSE_LEFTSINGLE, MOUSE_LEFTRELEASE = 0x20000000, 0x00020000
EVENT_BASE = 0x11000                              # THIRD_PARTY_EVENT_ID_MIN
F_O_SHIFT = 72                                    # EVT_CDU_R_x = EVT_CDU_L_x + 72

# FCD key -> EVT_CDU_L_* offset from EVENT_BASE
KEY_EVENTS: Dict[str, int] = {
    "INIT_REF": 546, "RTE": 547, "CLB": 548, "CRZ": 549, "DES": 550, "MENU": 551, "LEGS": 552, "DEP_ARR": 553,
    "HOLD": 554, "PROG": 555, "EXEC": 556, "N1_LIMIT": 557, "FIX": 558, "PREV_PAGE": 559, "NEXT_PAGE": 560,
    ".": 570, "0": 571, "+/-": 572, "SP": 599, "DEL": 600, "/": 601, "CLR": 602,
}
KEY_EVENTS.update({f"LSK{n}L": 533 + n for n in range(1, 7)})
KEY_EVENTS.update({f"LSK{n}R": 539 + n for n in range(1, 7)})
KEY_EVENTS.update({str(d): 560 + d for d in range(1, 10)})
KEY_EVENTS.update({c: 573 + i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")})


def log(message: str) -> None:
    print(f"[pmdg737] {time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


def event_for(key: str, cdu: int = CDU) -> Optional[int]:
    offset = KEY_EVENTS.get(key)
    return None if offset is None else EVENT_BASE + offset + (F_O_SHIFT if cdu else 0)


def render(raw: bytes) -> Optional[Page]:
    """PMDG_NG3_CDU_Screen bytes -> a page, or None while the CDU is unpowered."""
    if len(raw) < SCREEN_BYTES or not raw[SCREEN_BYTES - 1]:
        return None
    pg = Page()
    for col in range(COLUMNS):
        for row in range(ROWS):
            i = (col * ROWS + row) * 3
            symbol, color, flags = raw[i], raw[i + 1], raw[i + 2]
            if symbol in (0, 32):
                continue
            ch = SYMBOLS.get(symbol) or bytes([symbol]).decode("latin-1")
            name = "grey" if flags & FLAG_UNUSED else COLORS[color] if color < len(COLORS) else "white"
            pg.write(row, col, ch, name, small=bool(flags & FLAG_SMALL))
    return pg


def lights_from(raw: bytes, cdu: int = CDU) -> Dict[str, bool]:
    return {name: bool(raw[i * 2 + cdu]) for i, name in enumerate(ANNUN_ORDER) if i * 2 + cdu < len(raw)}


def waiting_page(line1: str, line2: str = "") -> Page:
    pg = Page().center(1, "PMDG 737 CDU", "cyan")
    pg.center(5, line1, "white")
    if line2:
        pg.center(7, line2, "white", small=True)
    pg.center(11, "FIRST OFFICER" if CDU else "CAPTAIN", "green", small=True)
    return pg


class SimConnectDll:
    """The few SimConnect calls this app needs, over ctypes."""

    RECV_EXCEPTION, RECV_OPEN, RECV_QUIT, RECV_CLIENT_DATA = 1, 2, 3, 16
    CLIENT_DATA_HEADER = 40                       # SIMCONNECT_RECV_CLIENT_DATA up to dwData

    def __init__(self, name: bytes = b"MOZA FCD PMDG 737 CDU"):
        import SimConnect as package
        dll = ctypes.WinDLL(os.path.join(os.path.dirname(package.__file__), "SimConnect.dll"))
        self.dll, self.h = dll, c_void_p()
        for fn, args in {
            "SimConnect_Open": [POINTER(c_void_p), c_char_p, c_void_p, c_uint32, c_void_p, c_uint32],
            "SimConnect_Close": [c_void_p],
            "SimConnect_MapClientDataNameToID": [c_void_p, c_char_p, c_uint32],
            "SimConnect_AddToClientDataDefinition": [c_void_p, c_uint32, c_uint32, c_uint32, c_float, c_uint32],
            "SimConnect_RequestClientData": [c_void_p] + [c_uint32] * 8,
            "SimConnect_MapClientEventToSimEvent": [c_void_p, c_uint32, c_char_p],
            "SimConnect_TransmitClientEvent": [c_void_p] + [c_uint32] * 5,
            "SimConnect_GetNextDispatch": [c_void_p, POINTER(c_void_p), POINTER(c_uint32)],
        }.items():
            getattr(dll, fn).argtypes = args
            getattr(dll, fn).restype = ctypes.c_long
        if dll.SimConnect_Open(byref(self.h), name, None, 0, None, 0) != 0:
            raise ConnectionError("SimConnect_Open failed (is the sim running?)")
        self._events: Dict[int, int] = {}

    def subscribe(self, area: bytes, area_id: int, definition: int, offset: int, size: int, request: int) -> None:
        """Changed-only updates of `size` bytes at `offset` in a client data area, checked every frame."""
        self.dll.SimConnect_MapClientDataNameToID(self.h, area, area_id)
        self.dll.SimConnect_AddToClientDataDefinition(self.h, definition, offset, size, 0.0, 0)
        self.dll.SimConnect_RequestClientData(self.h, area_id, request, definition, 2, 1, 0, 0, 0)

    def transmit(self, event: int, data: int) -> None:
        if event not in self._events:
            client_id = len(self._events) + 1
            self.dll.SimConnect_MapClientEventToSimEvent(self.h, client_id, f"#{event}".encode())
            self._events[event] = client_id
        # object 0 = the user aircraft; group 1 = highest priority, flag 0x10 = "group id is a priority"
        if self.dll.SimConnect_TransmitClientEvent(self.h, 0, self._events[event], data, 1, 0x10) != 0:
            raise ConnectionError("SimConnect_TransmitClientEvent failed")

    def next(self):
        """(kind, request_id, payload bytes) for the next message, or None when the queue is empty."""
        ptr, size = c_void_p(), c_uint32()
        if self.dll.SimConnect_GetNextDispatch(self.h, byref(ptr), byref(size)) != 0 or not ptr.value:
            return None
        raw = ctypes.string_at(ptr.value, size.value)
        kind = int.from_bytes(raw[8:12], "little")
        if kind == self.RECV_CLIENT_DATA:
            return kind, int.from_bytes(raw[12:16], "little"), raw[self.CLIENT_DATA_HEADER:]
        if kind == self.RECV_EXCEPTION:
            return kind, int.from_bytes(raw[12:16], "little"), raw
        return kind, 0, raw

    def close(self) -> None:
        if self.h:
            self.dll.SimConnect_Close(self.h)
            self.h = c_void_p()


class SimLink:
    """SimConnect on one thread: receives the CDU screen and annunciators, sends key events."""

    SCREEN, LIGHTS = 1, 2

    def __init__(self, cdu: int = CDU, connect=None):
        self.cdu = cdu
        self.connect = connect or SimConnectDll   # tests pass a fake
        self.connected = False
        self.screen_raw: Optional[bytes] = None
        self.lights: Dict[str, bool] = {}
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()

    def start(self) -> "SimLink":
        threading.Thread(target=self._loop, name="PMDG737-sim", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def send(self, event: int, data: int, not_before: float = 0.0) -> None:
        self._q.put((event, data, not_before))

    def _open(self):
        sc = self.connect()
        sc.subscribe(CDU_AREA[self.cdu], CDU_AREA_ID[self.cdu], CDU_DEFINITION[self.cdu], 0, SCREEN_BYTES,
                     self.SCREEN)
        sc.subscribe(DATA_AREA, DATA_AREA_ID, DATA_DEFINITION, ANNUN_OFFSET, 10, self.LIGHTS)
        return sc

    def _handle(self, message) -> bool:
        kind, request, payload = message
        if kind == SimConnectDll.RECV_CLIENT_DATA and request == self.SCREEN:
            self.screen_raw = payload[:SCREEN_BYTES]
        elif kind == SimConnectDll.RECV_CLIENT_DATA and request == self.LIGHTS:
            self.lights = lights_from(payload[:10], self.cdu)
        elif kind == SimConnectDll.RECV_EXCEPTION:
            log(f"SimConnect exception {request}")
        elif kind == SimConnectDll.RECV_QUIT:
            return False
        return True

    def _loop(self) -> None:
        sc, last_error = None, None
        while not self._stop.is_set():
            if sc is None:
                try:
                    sc = self._open()
                    self.connected, last_error = True, None
                    log("SimConnect connected")
                except Exception as e:
                    if str(e) != last_error:
                        last_error = str(e)
                        log(f"SimConnect: {type(e).__name__}: {e}")
                    self._stop.wait(3)
                    continue
            try:
                while True:
                    message = sc.next()
                    if message is None:
                        break
                    if not self._handle(message):
                        raise ConnectionError("the sim closed")
                try:
                    event, data, not_before = self._q.get(timeout=0.02)
                    delay = not_before - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    sc.transmit(event, data)
                except queue.Empty:
                    pass
            except Exception as e:
                log(f"sim link: {type(e).__name__}: {e} - reconnecting")
                try:
                    sc.close()
                except Exception:
                    pass
                sc, self.connected, self.screen_raw, self.lights = None, False, None, {}
                self._stop.wait(3)
        if sc is not None:
            sc.close()


class Pmdg737Cdu:
    def __init__(self, screen, sim: SimLink, clock=time.monotonic):
        self.screen, self.sim, self.clock = screen, sim, clock
        self.brightness: Optional[int] = None
        self._down: Dict[int, float] = {}
        self._lights: Dict[str, bool] = {}
        self._raw: Optional[bytes] = None
        self._page: Optional[Page] = None
        screen.on_key = self.on_key

    def on_key(self, name: str, pressed: bool) -> None:
        if name in ("BRT-", "BRT+"):
            if pressed:
                current = self.brightness if self.brightness is not None else 50
                self.brightness = max(0, min(100, current + (SCREEN_STEP if name == "BRT+" else -SCREEN_STEP)))
                self.screen.set_screen_brightness(self.brightness)
            return
        event = event_for(name, self.sim.cdu)
        if event is None:
            return
        if pressed:
            self._down[event] = self.clock()
            self.sim.send(event, MOUSE_LEFTSINGLE)
        else:
            started = self._down.pop(event, None)
            self.sim.send(event, MOUSE_LEFTRELEASE, (started + MIN_PRESS_S) if started is not None else 0.0)

    def frame(self) -> Page:
        if not self.sim.connected:
            return waiting_page("WAITING FOR THE SIM")
        raw = self.sim.screen_raw
        if raw is None:
            return waiting_page("WAITING FOR PMDG 737", "CHECK [SDK] IN OPTIONS")
        if raw is not self._raw:
            self._raw, self._page = raw, render(raw)
        return self._page if self._page is not None else Page()      # unpowered CDU: blank, like the real one

    def tick(self) -> None:
        self.screen.show(self.frame())
        lights = self.sim.lights if self.sim.connected else {}
        for light in ANNUN_ORDER:
            on = bool(lights.get(light))
            if self._lights.get(light) != on and self.screen.set_light(light, on):
                self._lights[light] = on

    def lights_off(self) -> None:
        for light in ANNUN_ORDER:
            self.screen.set_light(light, False)
        self._lights.clear()


def main(screen) -> None:
    sim = SimLink().start()
    cdu = Pmdg737Cdu(screen, sim)
    try:
        cdu.brightness = screen.screen_brightness()
    except Exception:
        pass
    try:
        while True:
            cdu.tick()
            time.sleep(0.05)
    finally:
        sim.stop()
        try:
            cdu.lights_off()
        except Exception:
            pass
