"""iFly 737 MAX 8 CDU on the MOZA MB7F FCD: the captain's CDU screen, keys and lights.

    python -m moza_fcd run --app apps/ifly_cdu/ifly_cdu.py     # real FCD (start before MOZA Cockpit)
    python -m moza_fcd gui --app apps/ifly_cdu/ifly_cdu.py     # emulator

Data comes from the iFly EFB's local web server (iFly-EFB.exe, Socket.IO on port 8084), the same feed its
web CDU uses: `ifly_data_update` -> gau.LSKChar / LSK_Color / LSK_SmallFont (2 CDUs x 14 x 24) and
CDU_<light>_Status; keys go back as `send_cdu_key {id, value1, value2}`. The EFB's CDU is always the
captain's (keys carry no side), so this mirrors the captain's CDU. Needs `pip install "python-socketio[client]"`.

Keys: every FCD key has the same iFly name, except CLB -> VNAV, CRZ -> FMC COMM, DES -> ATC (the MAX's
labels). BRT -/+ change the FCD's own screen brightness. Holding CLR clears the scratchpad, as on the real CDU.

Environment variables:
  IFLY_EFB_URL  = http://localhost:8084 (default)
  IFLY_GREY     = grey (default) - colour for grey text; set white on screen firmware older than 1.2.7.32
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from typing import Dict, Optional

from moza_fcd import Page, remap_color
from moza_fcd import keys as K

EFB_URL = os.environ.get("IFLY_EFB_URL", "http://localhost:8084")
CAPTAIN = 0
KEY_GAP_S = 0.04          # the web CDU spaces keys 40 ms apart; the EFB drops faster bursts
SCREEN_STEP = 10
LIGHTS = ("EXEC", "MSG", "FAIL", "CALL", "OFST")
CDU_FIELDS = ("LSKChar", "LSK_Color", "LSK_SmallFont", "CDU_Brightness_Status") + \
             tuple(f"CDU_{name}_Status" for name in LIGHTS)

# FCD key name -> iFly key id
RENAMED = {"CLB": "VNAV", "CRZ": "FMC_COMM", "DES": "ATC", ".": "PERIOD", "/": "SLASH", "+/-": "ADDMINUS"}
SAME = {"INIT_REF", "RTE", "DEP_ARR", "LEGS", "HOLD", "PROG", "N1_LIMIT", "FIX", "MENU", "EXEC",
        "PREV_PAGE", "NEXT_PAGE", "CLR", "DEL", "SP"} | set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def log(message: str) -> None:
    print(f"[ifly] {time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


def ifly_key(name: str) -> Optional[str]:
    """FCD key name -> iFly key id, or None for keys that stay on the FCD (BRT -/+)."""
    lsk = K.parse_lsk(name)
    if lsk:
        return f"LSK_{lsk[0]}{lsk[1]}"
    if name in RENAMED:
        return RENAMED[name]
    return name if name in SAME else None


# iFly colour codes (from its web CDU). Codes 5-13 draw a symbol instead of the cell's character.
COLORS = {0: "white", 1: "green", 2: "cyan", 3: "magenta", 4: "grey", 5: "grey", 6: "white", 7: "grey",
          8: "magenta", 9: "white", 10: "white", 11: "white", 12: "white", 13: "white"}
SYMBOLS = {5: "□", 6: "°", 7: "°", 8: "°", 9: "←", 10: "→", 11: "↑", 12: "↓", 13: "■"}


def _char(value) -> str:
    """One display character. Control characters become blanks: the iFly pads text with NULs
    (e.g. the scratchpad 'KORD\\0'), and a NUL sent to the FCD stops that row from updating."""
    if isinstance(value, int):
        value = chr(value) if 0 <= value < 0x110000 else " "
    if not isinstance(value, str) or not value:
        return " "
    ch = value[0]
    return " " if ord(ch) < 32 or ord(ch) == 127 else ch


def render(gau: dict, cdu: int = CAPTAIN) -> Page:
    """The iFly CDU grid as an FCD Page (same 14 x 24 layout)."""
    pg = Page()
    chars, colors, small = gau["LSKChar"][cdu], gau["LSK_Color"][cdu], gau["LSK_SmallFont"][cdu]
    for r in range(min(14, len(chars))):
        for c in range(min(24, len(chars[r]))):
            code = colors[r][c] if r < len(colors) and c < len(colors[r]) else 0
            ch = SYMBOLS.get(code) or _char(chars[r][c])
            if ch == " ":
                continue
            pg.write(r, c, ch, COLORS.get(code, "white"),
                     small=bool(small[r][c]) if r < len(small) and c < len(small[r]) else False)
    return pg


def lights_from(gau: dict, cdu: int = CAPTAIN) -> Dict[str, bool]:
    out = {}
    for name in LIGHTS:
        v = gau.get(f"CDU_{name}_Status")
        out[name] = bool(v[cdu]) if isinstance(v, list) and len(v) > cdu else False
    return out


def waiting_page(line1: str, line2: str = "") -> Page:
    pg = Page().center(1, "IFLY 737 MAX CDU", "cyan")
    pg.center(5, line1, "white")
    if line2:
        pg.center(7, line2, "white", small=True)
    pg.center(11, EFB_URL.replace("http://", "")[:24], "green", small=True)
    pg.center(13, "BRT -/+ STILL WORK", "green", small=True)
    return pg


class EfbLink:
    """Socket.IO client for the iFly EFB. Keeps the latest CDU data in `gau`; reconnects by itself."""

    def __init__(self, url: str = EFB_URL, client_factory=None):
        self.url = url
        self.client_factory = client_factory
        self.gau: Optional[dict] = None
        self.sdk_connected = False
        self.connected = False
        self.rejected = ""
        self.updated_at = 0.0
        self._keys: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()
        self._sio = None

    # --- lifecycle -----------------------------------------------------------------

    def start(self) -> "EfbLink":
        threading.Thread(target=self._connect_loop, name="iFly-EFB", daemon=True).start()
        threading.Thread(target=self._key_loop, name="iFly-keys", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._sio:
                self._sio.disconnect()
        except Exception:
            pass

    def _new_client(self):
        if self.client_factory:
            return self.client_factory()
        import socketio
        return socketio.Client(reconnection=False)    # _connect_loop reconnects

    def _connect_loop(self) -> None:
        sio = self._new_client()
        self._sio = sio
        sio.on("connect", self._on_connect)
        sio.on("disconnect", self._on_disconnect)
        sio.on("connection_rejected", self._on_rejected)
        sio.on("ifly_data_update", self.on_update)
        last_error = None
        while not self._stop.is_set():
            if not sio.connected:
                try:
                    sio.connect(self.url, transports=["websocket", "polling"], wait_timeout=5)
                except Exception as e:   # EFB not running yet - try again shortly
                    if str(e) != last_error:
                        last_error = str(e)
                        log(f"connect failed: {type(e).__name__}: {e}")
            self._stop.wait(3)

    def _on_connect(self) -> None:
        log("connected")
        self.connected, self.rejected = True, ""
        self._sio.emit("request_data")

    def _on_disconnect(self, *args) -> None:
        log(f"disconnected {args}")
        self.connected = False

    def _on_rejected(self, data) -> None:
        log(f"rejected: {data}")
        self.rejected = str((data or {}).get("reason", "rejected")).upper() if isinstance(data, dict) else "REJECTED"

    def on_update(self, data) -> None:
        if not isinstance(data, dict):
            return
        if "sdk_connected" in data:
            self.sdk_connected = bool(data["sdk_connected"])
        gau = data.get("gau")
        if isinstance(gau, dict) and "LSKChar" in gau:
            self.gau = {k: gau[k] for k in CDU_FIELDS if k in gau}
            self.updated_at = time.monotonic()

    # --- keys ----------------------------------------------------------------------

    def send_key(self, key_id: str, value1: int = 1, value2: int = 0) -> None:
        self._keys.put((key_id, value1, value2))

    def _key_loop(self) -> None:
        while not self._stop.is_set():
            try:
                key_id, v1, v2 = self._keys.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if self._sio and self._sio.connected:
                    self._sio.emit("send_cdu_key", {"id": key_id, "value1": v1, "value2": v2})
            except Exception:
                pass
            time.sleep(KEY_GAP_S)


class IflyCdu:
    """Mirrors the captain's CDU onto the FCD and forwards its keys."""

    def __init__(self, screen, link: EfbLink, clock=time.monotonic):
        self.screen, self.link, self.clock = screen, link, clock
        self._lights: Dict[str, bool] = {}
        self.brightness: Optional[int] = None
        screen.on_key = self.on_key

    def on_key(self, name: str, pressed: bool) -> None:
        if name in ("BRT-", "BRT+"):
            if pressed:
                self.step_brightness(-SCREEN_STEP if name == "BRT-" else SCREEN_STEP)
            return
        key_id = ifly_key(name)
        if key_id is None:
            return
        if key_id == "CLR":                  # down/up separately, so holding clears the scratchpad
            self.link.send_key("CLR", 0, 0 if pressed else 1)
        elif pressed:
            self.link.send_key(key_id, 1, 0)

    def step_brightness(self, delta: int) -> None:
        current = self.brightness if self.brightness is not None else 50
        self.brightness = max(0, min(100, current + delta))
        self.screen.set_screen_brightness(self.brightness)

    def frame(self) -> Page:
        link = self.link
        if link.rejected:
            return waiting_page("EFB REJECTED THE LINK", link.rejected[:24])
        if not link.connected:
            return waiting_page("WAITING FOR IFLY EFB", "START THE SIM + IFLY")
        if link.gau is None or not link.sdk_connected:
            return waiting_page("WAITING FOR AIRCRAFT", "LOAD THE IFLY 737 MAX")
        return render(link.gau)

    def tick(self) -> None:
        self.screen.show(self.frame())
        lights = lights_from(self.link.gau) if self.link.connected and self.link.gau else {n: False for n in LIGHTS}
        for name, on in lights.items():
            if self._lights.get(name) != on and self.screen.set_light(name, on):
                self._lights[name] = on

    def lights_off(self) -> None:
        for name in LIGHTS:
            self.screen.set_light(name, False)
        self._lights.clear()


def main(screen) -> None:
    try:
        remap_color("grey", os.environ.get("IFLY_GREY", "grey"))
    except ValueError:
        remap_color("grey", "white")
    link = EfbLink().start()
    cdu = IflyCdu(screen, link)
    try:
        cdu.brightness = screen.screen_brightness()
    except Exception:
        pass
    try:
        while True:
            cdu.tick()
            time.sleep(0.1)
    finally:
        link.stop()
        try:
            cdu.lights_off()
        except Exception:
            pass


if __name__ == "__main__":
    from moza_fcd import FcdScreen
    with FcdScreen() as fcd:
        fcd.wait_ready(15)
        main(fcd)
