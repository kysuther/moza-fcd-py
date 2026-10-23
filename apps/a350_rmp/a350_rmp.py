"""iniBuilds A350-style Radio Management Panel (RMP) for the MOZA MB7F FCD.

    python -m moza_fcd gui --app apps/a350_rmp/a350_rmp.py     # emulator
    python -m moza_fcd run --app apps/a350_rmp/a350_rmp.py     # real FCD

Modelled on iniBuilds' RMP guides (A350 / A321LR DRAIMS):
  VHF   active frequencies left, standby right. RSK = enter standby, LSK = swap.
  NAV   AUTO (FMS tunes) or STBY RAD NAV (manual) - toggled on LSK 2. RSK 1-3 open LS / VOR / ADF.
  ATC   XPDR system, ATC MODE AUTO/STBY, ALT RPTG, TCAS mode, traffic, IDENT, squawk on LSK 4.
        TCAS needs ATC MODE AUTO; full Mode C needs ALT RPTG ON.

FCD keys used as the RMP's shortcut keys (the MENU page shows this legend):
  INIT REF = VHF    RTE = HF    CLB = TEL    CRZ = SQWK/ATC    DES = NAV
  MENU = MENU       PREV/NEXT PAGE = cycle VHF -> NAV -> ATC
Entries use the CDU idiom: type into the scratchpad, then press the line's key.

Environment variables:
  RMP_BACKEND = auto (default) | msfs | local. "auto" uses MSFS through SimConnect when the
                `SimConnect` package is installed and the sim is running, else runs standalone.
  RMP_NUMBER  = 1 (captain, default) | 2 (first officer) | 3 (backup) - which aircraft RMP to drive.
See rmp_msfs.py for the sim mapping (standard events + iniBuilds A350 LVars).
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from moza_fcd.app import CduApp, CduPage

# ---------------------------------------------------------------------------
# Entry parsing / validation (pure functions)
# ---------------------------------------------------------------------------

_NUM = re.compile(r"^\d{1,3}(\.\d{0,3})?$|^\d{4,6}$")


def _split_mhz(entry: str):
    """'118.7' / '1187' / '118700' / '18.7' -> (mhz:int, frac_digits:str) or None."""
    s = entry.strip().replace(" ", "")
    if not _NUM.match(s):
        return None
    if "." in s:
        mhz, frac = s.split(".")
    elif len(s) > 3:
        mhz, frac = s[:3], s[3:]
    else:
        mhz, frac = s, ""
    if len(mhz) == 2:  # Airbus RMP lets you drop the leading 1: "18.7" -> 118.7
        mhz = "1" + mhz
    if len(mhz) != 3:
        return None
    return int(mhz), frac


def parse_com(entry: str) -> Optional[str]:
    """VHF COM channel, 118.000-136.990, 25 kHz or 8.33 kHz channel names -> '118.705'."""
    parts = _split_mhz(entry)
    if parts is None:
        return None
    mhz, frac = parts
    frac = (frac + "000")[:3]
    khz = int(frac)
    if not 118 <= mhz <= 136 or (mhz == 136 and khz > 990):
        return None
    if khz % 25 not in (0, 5, 10, 15):  # 8.33 channel names end in 05/10/15 within each 25 kHz block
        return None
    return f"{mhz}.{frac}"


def parse_nav(entry: str, kind: str) -> Optional[str]:
    """kind 'LS': 108.10-111.95 with an odd tenths digit; 'VOR': 108.00-117.95 in 50 kHz steps -> '110.30'."""
    parts = _split_mhz(entry)
    if parts is None:
        return None
    mhz, frac = parts
    if len(frac) > 2:
        return None
    frac = (frac + "00")[:2]
    hundredths = int(frac)
    if hundredths % 5:
        return None
    if kind == "LS":
        if not (108 <= mhz <= 111) or int(frac[0]) % 2 == 0:
            return None
    elif kind == "VOR":
        if not 108 <= mhz <= 117:
            return None
    else:
        raise ValueError(kind)
    return f"{mhz}.{frac}"


def parse_adf(entry: str) -> Optional[str]:
    """190-1750 kHz, optional .5 -> '350.0'."""
    m = re.match(r"^(\d{3,4})(?:\.([05]))?$", entry.strip())
    if not m:
        return None
    khz = int(m.group(1))
    if not 190 <= khz <= 1750:
        return None
    return f"{khz}.{m.group(2) or '0'}"


def parse_crs(entry: str) -> Optional[int]:
    if not re.match(r"^\d{1,3}$", entry.strip()):
        return None
    crs = int(entry)
    return crs % 360 if crs <= 360 else None


def parse_squawk(entry: str) -> Optional[str]:
    s = entry.strip()
    return s if re.match(r"^[0-7]{4}$", s) else None


# ---------------------------------------------------------------------------
# Radio state
# ---------------------------------------------------------------------------

TCAS_MODES = ("TA/RA", "TA ONLY", "STBY")   # selectable only with the transponder (ATC MODE) in AUTO
TRAFFIC_MODES = ("NORM", "ABV", "BLW")   # A350 pedestal (LVar INI_tcas_mode 1/2/3)
IDENT_SECONDS = 18.0


@dataclass
class ComRadio:
    active: str
    standby: str


@dataclass
class NavRadio:
    freq: str
    crs: int = 0


@dataclass
class RadioState:
    vhf: List[ComRadio] = field(default_factory=lambda: [
        ComRadio("118.700", "121.800"), ComRadio("121.500", "122.800"), ComRadio("131.725", "123.450")])
    tx: int = 1                         # VHF radio selected for transmit (1-3)
    nav_mode: str = "AUTO"              # "AUTO" (FMS tunes) or "STBY" (STBY RAD NAV)
    ls: NavRadio = field(default_factory=lambda: NavRadio("110.30", 245))
    vor: List[NavRadio] = field(default_factory=lambda: [NavRadio("114.70", 0), NavRadio("115.80", 0)])
    adf: List[str] = field(default_factory=lambda: ["350.0", "415.0"])
    xpdr_sys: int = 1
    atc_mode: str = "AUTO"              # "AUTO" or "STBY"
    alt_rptg: bool = True
    tcas: str = "TA/RA"
    traffic: str = "NORM"
    squawk: str = "2000"
    ident_until: float = 0.0

    @property
    def tcas_active(self) -> bool:
        return self.atc_mode == "AUTO" and self.tcas != "STBY"

    @property
    def xpdr_state(self) -> str:
        """What the sim's transponder ends up in: STBY, ON (mode A) or ALT (mode C)."""
        if self.atc_mode == "STBY":
            return "STBY"
        return "ALT" if self.alt_rptg else "ON"


class LocalBackend:
    """No simulator: the panel just keeps its own state."""
    name = "LOCAL"
    connected = True
    error = ""

    def can(self, what: str) -> bool:
        return True

    def start(self, state: RadioState, lock: threading.RLock, on_change) -> None: ...
    def stop(self) -> None: ...
    def set_com_standby(self, index: int, freq: str) -> None: ...
    def swap_com(self, index: int) -> None: ...
    def set_tx(self, index: int) -> None: ...
    def set_nav(self, which: str, index: int, freq: str) -> None: ...
    def set_crs(self, which: str, index: int, crs: int) -> None: ...
    def set_adf(self, index: int, khz: str) -> None: ...
    def set_squawk(self, code: str) -> None: ...
    def ident(self) -> None: ...
    def set_xpdr_state(self, state: str) -> None: ...
    def set_xpdr_sys(self, sys_no: int) -> None: ...
    def set_tcas(self, mode: str) -> None: ...
    def set_traffic(self, mode: str) -> None: ...


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

MAIN_CYCLE = ("vhf", "nav", "atc")


class RmpPage(CduPage):
    """Shared behaviour: the RMP's HF/TEL keys and PREV/NEXT cycling between main pages."""

    def __init__(self, rmp: "Rmp") -> None:
        super().__init__()
        self.rmp = rmp

    @property
    def s(self) -> RadioState:
        return self.rmp.state

    def on_key(self, app: CduApp, name: str) -> bool:
        if name in ("RTE", "CLB"):
            app.show_message(f"{'HF' if name == 'RTE' else 'TEL'} NOT AVAILABLE")
            return True
        if name in ("NEXT_PAGE", "PREV_PAGE"):
            here = self.rmp.main_page_name
            step = 1 if name == "NEXT_PAGE" else -1
            app.go(MAIN_CYCLE[(MAIN_CYCLE.index(here) + step) % len(MAIN_CYCLE)])
            return True
        return False

    def entry(self, app: CduApp, parse, *args):
        """Validate the scratchpad with parse(); on success take it, else show the CDU messages."""
        if not app.scratchpad:
            app.show_message("ENTER DATA IN SCRATCHPAD")
            return None
        value = parse(app.scratchpad, *args)
        if value is None:
            app.show_message("INVALID ENTRY")
            return None
        app.take_scratchpad()
        return value


class VhfPage(RmpPage):
    title = "VHF"

    def on_enter(self, app):
        self.rmp.main_page_name = "vhf"

    def draw(self, app, pg):
        s = self.s
        for i, radio in enumerate(s.vhf, start=1):
            tx = " TX" if s.tx == i else ""
            app.line(pg, i, radio.active, radio.standby, left_label=f"VHF{i}{tx}", right_label="STBY",
                     left_color="green", right_color="cyan", label_color="green" if tx else "white")
        app.line(pg, 4, f"<TX VHF{s.tx}", left_label="TRANSMIT")
        app.line(pg, 5, s.tcas if s.tcas_active else "STBY", s.squawk, left_label="TCAS", right_label="SQWK",
                 left_color="green" if s.tcas_active else "amber", right_color="green")
        app.line(pg, 6, "<MENU", "NAV>")

    def on_lsk(self, app, side, n):
        s, be = self.s, self.rmp.backend
        if side == "L" and n in (1, 2, 3):               # swap active <-> standby
            r = s.vhf[n - 1]
            r.active, r.standby = r.standby, r.active
            be.swap_com(n)
        elif side == "R" and n in (1, 2, 3):             # enter standby frequency
            freq = self.entry(app, parse_com)
            if freq:
                s.vhf[n - 1].standby = freq
                be.set_com_standby(n, freq)
        elif (side, n) == ("L", 4):                      # transmit key: VHF1 -> 2 -> 3
            s.tx = s.tx % 3 + 1
            be.set_tx(s.tx)
        elif (side, n) == ("L", 5):
            app.go("atc")
        elif (side, n) == ("R", 5):
            code = self.entry(app, parse_squawk)
            if code:
                s.squawk = code
                be.set_squawk(code)
        elif (side, n) == ("L", 6):
            app.go("menu")
        elif (side, n) == ("R", 6):
            app.go("nav")
        else:
            return False
        return True


class NavPage(RmpPage):
    title = "NAV"

    def on_enter(self, app):
        self.rmp.main_page_name = "nav"

    def draw(self, app, pg):
        s = self.s
        stby = s.nav_mode == "STBY"
        val = "green" if stby else "grey"
        app.line(pg, 1, f"{s.ls.freq}/{s.ls.crs:03d}", "LS>", left_label="LS FREQ/CRS", left_color=val)
        app.line(pg, 2, "<STBY RAD NAV" if stby else "<AUTO", "VOR>", left_label="RAD NAV MODE",
                 left_color="amber" if stby else "green")
        app.line(pg, 3, f"{s.vor[0].freq}/{s.vor[1].freq}", "ADF>", left_label="VOR1/VOR2", left_color=val)
        app.line(pg, 4, f"{s.adf[0]}/{s.adf[1]}", left_label="ADF1/ADF2", left_color=val)
        if not stby:
            pg.center(10, "FMS TUNED - LSK2 = STBY", "grey", small=True)
        app.line(pg, 6, "<MENU", "ATC>")

    def on_lsk(self, app, side, n):
        s = self.s
        if (side, n) == ("L", 2):
            s.nav_mode = "AUTO" if s.nav_mode == "STBY" else "STBY"
        elif side == "R" and n in (1, 2, 3):
            app.go(("ls", "vor", "adf")[n - 1])
        elif (side, n) == ("L", 6):
            app.go("menu")
        elif (side, n) == ("R", 6):
            app.go("atc")
        else:
            return False
        return True


class NavSubPage(RmpPage):
    """LS / VOR / ADF pages: entries only accepted in STBY RAD NAV."""

    def require_stby(self, app) -> bool:
        if self.s.nav_mode != "STBY":
            app.show_message("SELECT STBY RAD NAV")
            return False
        return True

    def colour(self) -> str:
        return "green" if self.s.nav_mode == "STBY" else "grey"

    def footer(self, app, pg):
        app.line(pg, 6, "<RETURN", "STBY RAD NAV" if self.s.nav_mode == "STBY" else "AUTO",
                 right_color="amber" if self.s.nav_mode == "STBY" else "green")


class LsPage(NavSubPage):
    title = "NAV - LS"

    def draw(self, app, pg):
        app.line(pg, 1, self.s.ls.freq, f"{self.s.ls.crs:03d}", left_label="FREQ", right_label="CRS",
                 color=self.colour())
        self.footer(app, pg)

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            app.go("nav")
        elif n == 1 and self.require_stby(app):
            if side == "L":
                freq = self.entry(app, parse_nav, "LS")
                if freq:
                    self.s.ls.freq = freq
                    self.rmp.backend.set_nav("LS", 1, freq)
            else:
                crs = self.entry(app, parse_crs)
                if crs is not None:
                    self.s.ls.crs = crs
                    self.rmp.backend.set_crs("LS", 1, crs)
        return True


class VorPage(NavSubPage):
    title = "NAV - VOR"

    def draw(self, app, pg):
        for i, vor in enumerate(self.s.vor, start=1):
            app.line(pg, i, vor.freq, f"{vor.crs:03d}", left_label=f"VOR{i} FREQ", right_label="CRS",
                     color=self.colour())
        self.footer(app, pg)

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            app.go("nav")
        elif n in (1, 2) and self.require_stby(app):
            vor = self.s.vor[n - 1]
            if side == "L" and not self.rmp.backend.can("vor_freq"):
                app.show_message("TUNE VOR IN COCKPIT")
            elif side == "L":
                freq = self.entry(app, parse_nav, "VOR")
                if freq:
                    vor.freq = freq
                    self.rmp.backend.set_nav("VOR", n, freq)
            else:
                crs = self.entry(app, parse_crs)
                if crs is not None:
                    vor.crs = crs
                    self.rmp.backend.set_crs("VOR", n, crs)
        return True


class AdfPage(NavSubPage):
    title = "NAV - ADF"

    def draw(self, app, pg):
        for i, khz in enumerate(self.s.adf, start=1):
            app.line(pg, i, khz, left_label=f"ADF{i} FREQ", color=self.colour())
        self.footer(app, pg)

    def on_lsk(self, app, side, n):
        if (side, n) == ("L", 6):
            app.go("nav")
        elif side == "L" and n in (1, 2) and not self.rmp.backend.can("adf_freq"):
            app.show_message("TUNE ADF IN COCKPIT")
        elif side == "L" and n in (1, 2) and self.require_stby(app):
            khz = self.entry(app, parse_adf)
            if khz:
                self.s.adf[n - 1] = khz
                self.rmp.backend.set_adf(n, khz)
        return True


class AtcPage(RmpPage):
    title = "ATC / TCAS"

    def on_enter(self, app):
        self.rmp.main_page_name = "atc"

    def draw(self, app, pg):
        s = self.s
        app.line(pg, 1, f"<SYS {s.xpdr_sys}", f"{s.atc_mode}>", left_label="XPDR", right_label="ATC MODE",
                 right_color="green" if s.atc_mode == "AUTO" else "amber")
        tcas_txt = s.tcas if s.atc_mode == "AUTO" else f"{s.tcas} INOP"
        app.line(pg, 2, "<ON" if s.alt_rptg else "<OFF", f"{tcas_txt}>", left_label="ALT RPTG", right_label="TCAS",
                 left_color="green" if s.alt_rptg else "amber",
                 right_color="green" if s.tcas_active else "amber")
        identing = time.time() < s.ident_until
        app.line(pg, 3, "" if identing else "<IDENT", f"{s.traffic}>", right_label="TRAFFIC")
        if identing:
            pg.write(6, 0, "TRANSMITTING IDENT", "amber")
        app.line(pg, 4, s.squawk, left_label="SQWK", left_color="green")
        notes = []
        if s.atc_mode != "AUTO":
            notes.append("TCAS NEEDS ATC MODE AUTO")
        if not s.alt_rptg:
            notes.append("ALT RPTG OFF: NO MODE C")
        for i, note in enumerate(notes[:2]):
            pg.center(9 + i, note, "amber", small=True)
        app.line(pg, 6, "<MENU", "VHF>")

    def on_lsk(self, app, side, n):
        s, be = self.s, self.rmp.backend
        if (side, n) == ("L", 1):
            s.xpdr_sys = 2 if s.xpdr_sys == 1 else 1
            be.set_xpdr_sys(s.xpdr_sys)
        elif (side, n) in (("R", 1), ("L", 2)) and not be.can("xpdr_state"):
            app.show_message("SET ATC MODE IN COCKPIT")   # read-only through the sim link
        elif (side, n) == ("R", 1):
            s.atc_mode = "STBY" if s.atc_mode == "AUTO" else "AUTO"
            be.set_xpdr_state(s.xpdr_state)
        elif (side, n) == ("L", 2):
            s.alt_rptg = not s.alt_rptg
            be.set_xpdr_state(s.xpdr_state)
        elif (side, n) == ("R", 2):
            if s.atc_mode != "AUTO":            # as on the A350: TCAS modes only change with the transponder in AUTO
                app.show_message("ATC MODE AUTO REQD")
            else:
                s.tcas = TCAS_MODES[(TCAS_MODES.index(s.tcas) + 1) % len(TCAS_MODES)]
                be.set_tcas(s.tcas)
        elif (side, n) == ("L", 3):
            if s.atc_mode != "AUTO":
                app.show_message("ATC MODE AUTO REQD")
            elif time.time() >= s.ident_until:
                s.ident_until = time.time() + IDENT_SECONDS
                be.ident()
        elif (side, n) == ("R", 3):
            s.traffic = TRAFFIC_MODES[(TRAFFIC_MODES.index(s.traffic) + 1) % len(TRAFFIC_MODES)]
            be.set_traffic(s.traffic)
        elif (side, n) == ("L", 4):
            code = self.entry(app, parse_squawk)
            if code:
                s.squawk = code
                be.set_squawk(code)
        elif (side, n) == ("L", 6):
            app.go("menu")
        elif (side, n) == ("R", 6):
            app.go("vhf")
        else:
            return False
        return True


class MenuPage(RmpPage):
    title = "RMP MENU"

    def draw(self, app, pg):
        be = self.rmp.backend
        app.line(pg, 1, "<VHF", "INIT REF", right_label="KEY", color="white", right_color="grey")
        app.line(pg, 2, "<NAV", "DES", right_color="grey")
        app.line(pg, 3, "<ATC / SQWK", "CRZ", right_color="grey")
        status = "MSFS CONNECTED" if be.name == "MSFS" and be.connected else (
            "MSFS NOT CONNECTED" if be.name == "MSFS" else "STANDALONE")
        app.line(pg, 4, status, left_label="SIM LINK",
                 left_color="green" if be.connected and be.name == "MSFS" else ("amber" if be.name == "MSFS" else "cyan"))
        app.line(pg, 5, f"RMP {self.rmp.rmp_number} ({('CAPT', 'F/O', 'BACKUP')[self.rmp.rmp_number - 1]})",
                 left_label="PANEL")
        app.line(pg, 6, "", "PREV/NEXT CYCLES", right_color="grey")

    def on_lsk(self, app, side, n):
        if side == "L" and n in (1, 2, 3):
            app.go(MAIN_CYCLE[n - 1])
            return True
        return False


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class Rmp:
    def __init__(self, screen, backend=None, rmp_number: int = 1) -> None:
        self.state = RadioState()
        self.backend = backend or LocalBackend()
        self.rmp_number = rmp_number
        self.main_page_name = "vhf"
        self.app = CduApp(screen, home="vhf", refresh_s=0.5, function_keys={
            "INIT_REF": "vhf", "CRZ": "atc", "DES": "nav", "MENU": "menu"})
        for name, page in (("vhf", VhfPage(self)), ("nav", NavPage(self)), ("ls", LsPage(self)),
                           ("vor", VorPage(self)), ("adf", AdfPage(self)), ("atc", AtcPage(self)),
                           ("menu", MenuPage(self))):
            self.app.add(name, page)
        self._fail_shown = None

    def tick(self) -> None:
        """FAIL light when a sim link was requested but isn't connected."""
        fail = self.backend.name == "MSFS" and not self.backend.connected
        if fail != self._fail_shown:
            self._fail_shown = fail
            self.app.screen.set_light("FAIL", fail)


def rmp_number() -> int:
    """Which aircraft RMP this panel drives: 1 captain (default), 2 first officer, 3 backup."""
    try:
        n = int(os.environ.get("RMP_NUMBER", "1"))
    except ValueError:
        n = 1
    return n if n in (1, 2, 3) else 1


def make_backend():
    choice = os.environ.get("RMP_BACKEND", "auto").lower()
    if choice == "local":
        return LocalBackend()
    try:
        from rmp_msfs import MsfsBackend  # sibling module; the app loader puts this folder on sys.path
        backend = MsfsBackend(required=(choice == "msfs"), rmp_number=rmp_number())
        print(f"RMP: MSFS link (RMP {rmp_number()}) - follows the aircraft once the sim is running", file=sys.stderr)
        return backend
    except ImportError as e:
        if choice == "msfs":
            raise
        print(f"RMP: standalone - NOT linked to the sim ({e}). Install it with: pip install SimConnect",
              file=sys.stderr)
        return LocalBackend()


def main(screen) -> None:
    backend = make_backend()
    rmp = Rmp(screen, backend, rmp_number())
    backend.start(rmp.state, rmp.app._lock, rmp.app.refresh)
    stop = threading.Event()

    def ticker():
        while not stop.wait(0.5):
            try:
                rmp.tick()
            except Exception:
                pass

    threading.Thread(target=ticker, daemon=True).start()
    try:
        rmp.app.run()
    finally:
        stop.set()
        backend.stop()
