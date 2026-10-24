"""BlueBird 757 CDU on the MOZA MB7F FCD: the captain's CDU screen, keys and lights.

    python -m moza_fcd run --app apps/bb757_cdu/bb757_cdu.py     # real FCD (start before MOZA Cockpit)

Screen: the 757 pushes its CDU itself. Its tablet code connects to MobiFlight's CDU interface,
ws://localhost:8320/winwing/cdu-captain, retrying every 5 s, and sends {"Target":"Font","Data":"Boeing"} and then
{"Target":"Display","Data":[336 cells of [char, colour, small]]} whenever the screen changes. This app is that
server, so MobiFlight Connector / the WINGFLEX app must not be running (only one program can hold the port).
Keys: held L-vars L:B757_CDU_1_<KEY> (1 while pressed, 0 on release), written through SimConnect.
Lights: L:FMC_EXEC_ACTIVE_1 and L:B757_CDU1_<MSG|FAIL|OFST|DSPY>_decal (plus the overhead light test).
Needs `pip install SimConnect websockets`.

Keys: as labelled, except the 757's newer CDU layout: CLB -> VNAV, CRZ -> FMC COMM, DES -> ATC,
N1 LIMIT -> NAV RAD. BRT -/+ set the FCD's own brightness. The 757's DSPY light shows on CALL.

Environment: BB757_PORT = 8320 (default).
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
import time
from typing import Dict, Optional

from moza_fcd import Page

PORT = int(os.environ.get("BB757_PORT", "8320"))
PATH = "/winwing/cdu-captain"
MIN_PRESS_S = 0.1          # hold each key at least this long so the aircraft sees it
LIGHT_POLL_S = 0.3
SCREEN_STEP = 10

# FCD key -> 757 key name (L:B757_CDU_1_<name>)
KEY_MAP: Dict[str, str] = {
    "INIT_REF": "INIT", "RTE": "RTE", "DEP_ARR": "DEPARR", "LEGS": "LEGS", "HOLD": "HOLD", "PROG": "PROG",
    "FIX": "FIX", "MENU": "MENU", "PREV_PAGE": "PREV", "NEXT_PAGE": "NEXT", "EXEC": "EXEC",
    "CLB": "VNAV", "CRZ": "FMCCOMM", "DES": "ATC", "N1_LIMIT": "NAVRAD",     # the 757's newer CDU layout
    ".": "NUMPER", "+/-": "NUMPLUS", "/": "KEYSLASH", "SP": "KEYSP", "DEL": "KEYDEL", "CLR": "KEYCLR",
}
KEY_MAP.update({c: f"KEY{c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"})
KEY_MAP.update({d: f"NUM{d}" for d in "0123456789"})
KEY_MAP.update({f"LSK{n}{s}": f"{s}{n}" for n in range(1, 7) for s in "LR"})

# FCD light -> 757 L-vars (lit if any is set; the overhead light test lights the four annunciators)
LIGHTS = {"EXEC": ("FMC_EXEC_ACTIVE_1",), "MSG": ("B757_CDU1_MSG_decal", "OVHD_TEST_LTS"),
          "FAIL": ("B757_CDU1_FAIL_decal", "OVHD_TEST_LTS"), "OFST": ("B757_CDU1_OFST_decal", "OVHD_TEST_LTS"),
          "CALL": ("B757_CDU1_DSPY_decal", "OVHD_TEST_LTS")}
LIGHT_LVARS = sorted({v for vs in LIGHTS.values() for v in vs})
SYMBOLS = {"☐": "□"}       # MobiFlight's box prompt -> the FCD font's box


def log(message: str) -> None:
    print(f"[bb757] {time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


def lvar_for(key: str) -> Optional[str]:
    name = KEY_MAP.get(key)
    return f"B757_CDU_1_{name}" if name else None


def render(message: dict) -> Page:
    """A MobiFlight Display message -> an FCD page."""
    data = [[SYMBOLS.get(str(c[0])[:1], c[0])] + list(c[1:]) if c else c for c in message.get("Data") or []]
    return Page.from_winwing_json({"Data": data})


def lights_from(values: Dict[str, float]) -> Dict[str, bool]:
    return {light: any((values.get(v) or 0) > 0.5 for v in lvars) for light, lvars in LIGHTS.items()}


def waiting_page(line1: str, line2: str = "") -> Page:
    pg = Page().center(1, "BLUEBIRD 757 CDU", "green")
    pg.center(5, line1, "white")
    if line2:
        pg.center(7, line2, "white", small=True)
    pg.center(11, f"ws://localhost:{PORT}"[:24], "green", small=True)
    return pg


class ScreenServer:
    """The WebSocket server the 757 connects to (MobiFlight's CDU interface)."""

    def __init__(self, port: int = PORT):
        self.port = port
        self.page: Optional[Page] = None
        self.clients = 0
        self.error = ""
        self._loop = None
        self._stop = None

    def start(self) -> "ScreenServer":
        threading.Thread(target=self._run, name="BB757-ws", daemon=True).start()
        return self

    def stop(self) -> None:
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)

    def on_message(self, text) -> None:
        try:
            message = json.loads(text)
        except (TypeError, ValueError):
            return
        if isinstance(message, dict) and message.get("Target") == "Display":
            self.page = render(message)

    async def _handler(self, ws) -> None:
        path = getattr(getattr(ws, "request", None), "path", PATH)
        if path != PATH:
            log(f"ignored a client on {path}")
            await ws.close()
            return
        self.clients += 1
        log("757 connected")
        try:
            async for text in ws:
                self.on_message(text)
        except Exception:
            pass
        finally:
            self.clients -= 1
            log("757 disconnected")

    def _run(self) -> None:
        import websockets

        async def serve():
            self._loop, self._stop = asyncio.get_running_loop(), asyncio.Event()
            try:
                async with websockets.serve(self._handler, "localhost", self.port):
                    log(f"listening on ws://localhost:{self.port}{PATH}")
                    await self._stop.wait()
            except OSError as e:
                self.error = f"PORT {self.port} IN USE"
                log(f"can't listen on port {self.port}: {e} (is MobiFlight or the WINGFLEX app running?)")
        asyncio.run(serve())


class SimLink:
    """SimConnect on one thread: writes the key L-vars, reads the light L-vars."""

    def __init__(self, connect=None):
        self.connect = connect            # -> (read(lvar) -> value, write(lvar, value)); for tests
        self.ready = False
        self.lights: Dict[str, bool] = {}
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()

    def start(self) -> "SimLink":
        threading.Thread(target=self._loop, name="BB757-sim", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def write(self, lvar: str, value: int, not_before: float = 0.0) -> None:
        self._q.put((lvar, value, not_before))

    def _open(self):
        if self.connect:
            return self.connect()
        from SimConnect import SimConnect
        from SimConnect.RequestList import Request
        sm = SimConnect()
        cache = {}

        def req(lvar):
            if lvar not in cache:
                cache[lvar] = Request((f"L:{lvar}".encode(), b"Number"), sm, _settable=True, _time=0)
            return cache[lvar]

        def read(lvar):
            return req(lvar).value

        def write(lvar, value):
            req(lvar).value = value
        return read, write

    def _loop(self) -> None:
        read = write = None
        last_error = None
        while not self._stop.is_set():
            if read is None:
                try:
                    read, write = self._open()
                    self.ready, last_error = True, None
                    log("SimConnect connected")
                except Exception as e:
                    if str(e) != last_error:
                        last_error = str(e)
                        log(f"SimConnect: {type(e).__name__}: {e}")
                    self._stop.wait(3)
                    continue
            try:
                try:
                    lvar, value, not_before = self._q.get(timeout=LIGHT_POLL_S)
                    delay = not_before - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
                    write(lvar, value)
                    continue                      # keys first; lights when idle
                except queue.Empty:
                    pass
                self.lights = lights_from({v: read(v) for v in LIGHT_LVARS})
            except Exception as e:
                log(f"sim link: {type(e).__name__}: {e} - reconnecting")
                read = write = None
                self.ready = False


class Bb757Cdu:
    def __init__(self, screen, server: ScreenServer, sim: SimLink, clock=time.monotonic):
        self.screen, self.server, self.sim, self.clock = screen, server, sim, clock
        self.brightness: Optional[int] = None
        self._down: Dict[str, float] = {}
        self._lights: Dict[str, bool] = {}
        screen.on_key = self.on_key

    def on_key(self, name: str, pressed: bool) -> None:
        if name in ("BRT-", "BRT+"):
            if pressed:
                current = self.brightness if self.brightness is not None else 50
                self.brightness = max(0, min(100, current + (SCREEN_STEP if name == "BRT+" else -SCREEN_STEP)))
                self.screen.set_screen_brightness(self.brightness)
            return
        lvar = lvar_for(name)
        if lvar is None:
            return
        if pressed:
            self._down[lvar] = self.clock()
            self.sim.write(lvar, 1)
        else:
            started = self._down.pop(lvar, None)
            self.sim.write(lvar, 0, (started + MIN_PRESS_S) if started is not None else 0.0)

    def frame(self) -> Page:
        if self.server.error:
            return waiting_page(self.server.error, "CLOSE MOBIFLIGHT/WINGFLEX")
        if not self.server.clients:
            return waiting_page("WAITING FOR THE 757", "LOAD THE BLUEBIRD 757")
        if self.server.page is None:
            return waiting_page("WAITING FOR CDU DATA")
        return self.server.page

    def tick(self) -> None:
        self.screen.show(self.frame())
        lights = self.sim.lights if (self.sim.ready and self.server.clients) else {}
        for light in LIGHTS:
            on = bool(lights.get(light))
            if self._lights.get(light) != on and self.screen.set_light(light, on):
                self._lights[light] = on

    def lights_off(self) -> None:
        for light in LIGHTS:
            self.screen.set_light(light, False)
        self._lights.clear()


def main(screen) -> None:
    server = ScreenServer().start()
    sim = SimLink().start()
    cdu = Bb757Cdu(screen, server, sim)
    try:
        cdu.brightness = screen.screen_brightness()
    except Exception:
        pass
    try:
        while True:
            cdu.tick()
            time.sleep(0.1)
    finally:
        server.stop()
        sim.stop()
        try:
            cdu.lights_off()
        except Exception:
            pass
