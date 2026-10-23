"""MSFS link for the A350 RMP app, via SimConnect (pip install SimConnect).

Built from MSFS SDK event/SimVar names and the iniBuilds A350 LVar list (FEB 2025 PDF,
forum topic 25015). Live-tested on MSFS 2024 with the iniBuilds A350 (2026-10-09) - see
LIVE_RESULTS below and the app README.

Mapping:
  VHF1-3 standby / swap         COM_STBY_RADIO_SET_HZ / COM2_ / COM3_, COM_STBY_RADIO_SWAP / COM2_RADIO_SWAP /
                                COM3_RADIO_SWAP. iniBuilds: VHF1/2/3 drive the sim's COM1/2/3.
                                RMP_FREQ_INPUT=keys instead presses the aircraft's own RMP keys (see below).
  VHF transmit select           L:INI_RMP<y>_TRANSMIT_CHANNEL (1-9; 1-3 = VHF1-3, confirmed in the cockpit), plus
                                L:INI_RMP<y>_<n>_RECEIVE_SEL = 1 so the radio you talk on is also heard (else the A350
                                shows "VHF<n> RECEPTION NOT SELECTED")
  VOR1/2, ADF freq              read-only: NAV1/2_RADIO_SET_HZ and ADF_COMPLETE_SET are ignored by the A350
                                (RMP in AUTO or STBY RAD NAV) - tune them in the cockpit
  VOR / LS course               panel-only: VOR1/2_SET and NAV OBS writes have no effect on the A350
  LS freq                       NAV<ls_nav_index>_RADIO_SET_HZ (default NAV3; RMP_LS_NAV=1..4). LS course: not sent.
  ADF1/2                        ADF_COMPLETE_SET / ADF2_COMPLETE_SET (BCD Hz)
  Squawk / IDENT                XPNDR_SET (BCD16) / XPNDR_IDENT_ON
  ATC MODE + ALT RPTG           read-only from TRANSPONDER STATE:1 (1 STBY, 3 ON, 4 ALT, 5 GND = AUTO on the ground):
                                MSFS refuses writes to it,
                                there is no state-set event, and the LVar list has nothing for them - set them
                                in the cockpit
  TCAS mode                     L:INI_tcas_mode_pedestal  0 STBY, 2 TA/RA, 3 TA ONLY - mapped by watching the LVar
                                while modes were selected in the cockpit (the PDF's 1/2/3 is wrong). TCAS can only be
                                changed with the transponder (ATC MODE) in AUTO; in STBY the aircraft forces it to 0.
                                Writes work with ATC MODE AUTO; with the transponder in STBY the aircraft reverts them.
  Traffic display               L:INI_tcas_mode           1 NORM, 2 ABV, 3 BLW
  XPDR system                   L:INI_XPDR_SYS            1 / 2
  y = RMP number: 1 captain, 2 first officer, 3 backup (env RMP_NUMBER). Key LVars use CPT / FO / OVHD.

Key-press input (RMP_FREQ_INPUT=keys, experimental): entering a VHF standby frequency presses
L:RMP_<x>_VHF_KEY, L:RMP_<x>_RSK_<n>_KEY, then L:RMP_<x>_NUM_<d>_KEY for each of the 6 digits
(the real RMP has no decimal key); swap presses L:RMP_<x>_LSK_<n>_KEY. These are the aircraft's
own auto-resetting key LVars, so its RMP logic does the tuning. Whether a 6th digit commits the
entry - live-tested: the 6th digit commits it (RMP_KEYS_CONFIRM=1 adds a second RSK press anyway).
"""

from __future__ import annotations

import os
import queue
import threading
import time
from typing import Callable, Optional

XPDR_STATE_VALUES = {"STBY": 1, "ON": 3, "ALT": 4}
COM_STBY_SET = ("COM_STBY_RADIO_SET_HZ", "COM2_STBY_RADIO_SET_HZ", "COM3_STBY_RADIO_SET_HZ")
COM_SWAP = ("COM_STBY_RADIO_SWAP", "COM2_RADIO_SWAP", "COM3_RADIO_SWAP")
ADF_SET = ("ADF_COMPLETE_SET", "ADF2_COMPLETE_SET")

TCAS_LVAR, TCAS_VALUES = "INI_tcas_mode_pedestal", {"STBY": 0, "TA/RA": 2, "TA ONLY": 3}  # observed in the cockpit
TCAS_READ = {v: k for k, v in TCAS_VALUES.items()}


def tcas_from_sim(raw) -> Optional[str]:
    """0 STBY, 2 TA/RA, 3 TA ONLY; anything else (1 was never observed) -> None."""
    if raw is None:
        return None
    return TCAS_READ.get(int(round(float(raw))))
TRAFFIC_LVAR, TRAFFIC_VALUES = "INI_tcas_mode", {"NORM": 1, "ABV": 2, "BLW": 3}
XPDR_SYS_LVAR = "INI_XPDR_SYS"
KEY_PANEL = {1: "CPT", 2: "FO", 3: "OVHD"}
KEY_PRESS_GAP_S = 0.12  # auto-reset key LVars: leave the aircraft a few frames between presses

# What a live run against MSFS 2024 + iniBuilds A350 showed (aircraft powered, on the ground,
# XPDR in STBY, RMP NAV in AUTO). Each write was read back after 2 s, then restored.
LIVE_RESULTS = {
    "COM standby (25 kHz and 8.33 kHz), swap": "works; reads back as channel names",
    "LS frequency on NAV3": "works",
    "VOR (NAV1/2) and ADF frequency": "reverted - FMS tunes them while the RMP is in AUTO",
    "VOR course (VOR1/2_SET, NAV OBS)": "no effect",
    "L:INI_XPDR_SYS": "writes stick",
    "L:INI_RMP1_TRANSMIT_CHANNEL": "works: 2 lights the VHF2 transmit key (needs RECEIVE_SEL too)",
    "L:INI_tcas_mode (traffic)": "writes stick; default 1 = NORM",
    "L:INI_tcas_mode_pedestal (TCAS)": "0 STBY, 2 TA/RA, 3 TA ONLY (watched in the cockpit); writing 2 selected "
                                        "TA/RA in the cockpit; needs ATC MODE AUTO, forced to 0 in STBY",
    "Squawk (XPNDR_SET)": "works",
    "Transponder state (SimVar write, XPNDR_STATE_SET)": "refused / no such event - read-only; reads 5 (GND) in AUTO on the ground",
    "IDENT (XPNDR_IDENT_ON)": "works with ATC MODE AUTO",
    "Key-press mode (RMP_CPT_*_KEY)": "works: VHF, RSK n, 6 digits commits; key LVars auto-reset",
    "VOR / ADF in STBY RAD NAV": "still ignored (the aircraft greys out LS/VOR on its RMP)",
    "All reads (COM/NAV/XPDR SimVars, LVars)": "work",
}


def com_to_hz(channel: str) -> int:
    """'118.705' -> 118705000. MSFS accepts 8.33 kHz channel names in Hz form."""
    mhz, frac = channel.split(".")
    return int(mhz) * 1_000_000 + int(frac) * 1000


def mhz_to_channel(mhz: float) -> str:
    """Sim COM frequency (MHz) -> channel name. Real 8.33 frequencies (e.g. 118.00833) map back to
    their channel names (118.010); exact 5 kHz values are kept as they are."""
    khz = mhz * 1000.0
    nearest5 = round(khz / 5) * 5
    if abs(khz - nearest5) < 0.5:
        name = int(nearest5)
    else:
        block = int(khz // 25) * 25
        idx = min(2, max(0, round((khz - block) / (25 / 3))))
        name = block + 5 * (idx + 1)
    return f"{name // 1000}.{name % 1000:03d}"


def nav_to_hz(freq: str) -> int:
    return int(round(float(freq) * 1_000_000))


def bcd(value: int) -> int:
    """Decimal digits as BCD: 2000 -> 0x2000, 350000 -> 0x350000."""
    return int(str(value), 16)


def bcd_to_squawk(value) -> Optional[str]:
    """TRANSPONDER CODE is BCO16 (0x2000 for 2000) -> '2000'; None if it isn't a valid squawk."""
    code = f"{int(value):04x}"
    return code if len(code) == 4 and all(c in "01234567" for c in code) else None


def _reverse(mapping: dict, value) -> Optional[str]:
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return next((k for k, n in mapping.items() if n == v), None)


# Units for the SimVars the bridge uses (SimConnect needs one per data definition).
SIMVAR_UNITS = {
    "COM ACTIVE FREQUENCY": "MHz", "COM STANDBY FREQUENCY": "MHz", "NAV ACTIVE FREQUENCY": "MHz",
    "NAV OBS": "Degrees", "TRANSPONDER CODE": "BCO16", "TRANSPONDER STATE": "Number",
}


class _RequestCache:
    """One SimConnect Request per exact variable name. (The package's AircraftRequests shares a single
    Request across indices and rewrites its name, which reads the first index for all of them.)"""

    def __init__(self, sm) -> None:
        from SimConnect.RequestList import Request
        self._Request, self._sm, self._cache = Request, sm, {}

    def _req(self, name: str, unit: str):
        if name not in self._cache:
            self._cache[name] = self._Request((name.encode(), unit.encode()), self._sm, _settable=True, _time=200)
        return self._cache[name]


class SimVars(_RequestCache):
    """get/set by key "COM_ACTIVE_FREQUENCY:1" (underscores -> spaces, like the package's keys)."""

    def _resolve(self, key: str):
        base, _, index = key.partition(":")
        base = base.replace("_", " ")
        return (f"{base}:{index}" if index else base), SIMVAR_UNITS.get(base, "Number")

    def get(self, key: str):
        return self._req(*self._resolve(key)).value

    def set(self, key: str, value) -> None:
        self._req(*self._resolve(key)).value = value


class LVars(_RequestCache):
    """L: variables as SimConnect data definitions with "L:" names."""

    def get(self, name: str):
        return self._req(f"L:{name}", "Number").value

    def set(self, name: str, value) -> None:
        self._req(f"L:{name}", "Number").value = value


class SimEvents:
    """find(name) for any key event name - the package's own list lacks many radio events."""

    def __init__(self, sm) -> None:
        from SimConnect.EventList import Event
        self._Event, self._sm, self._cache = Event, sm, {}

    def find(self, name: str):
        if name not in self._cache:
            self._cache[name] = self._Event(name.encode(), self._sm)
        return self._cache[name]


class MsfsBackend:
    name = "MSFS"

    def __init__(self, required: bool = False, ls_nav_index: Optional[int] = None,
                 simconnect_factory: Optional[Callable] = None, poll_s: float = 0.5,
                 rmp_number: int = 1, freq_input: Optional[str] = None) -> None:
        """simconnect_factory() -> (requests, events, lvars): get/set/find like the SimConnect package's
        AircraftRequests/AircraftEvents, plus an LVars-like object. Defaults to the real package
        (ImportError if it isn't installed)."""
        if simconnect_factory is None:
            from SimConnect import SimConnect  # ImportError -> no MSFS

            def simconnect_factory():
                sm = SimConnect()
                self._sm = sm
                return SimVars(sm), SimEvents(sm), LVars(sm)
        self._sm = None
        self._factory = simconnect_factory
        self.required = required
        self.ls_nav = ls_nav_index or int(os.environ.get("RMP_LS_NAV", "3"))
        self.rmp_number = rmp_number if rmp_number in KEY_PANEL else 1
        self.key_panel = KEY_PANEL[self.rmp_number]
        self.freq_input = (freq_input or os.environ.get("RMP_FREQ_INPUT", "events")).lower()
        self.keys_confirm = os.environ.get("RMP_KEYS_CONFIRM", "0") == "1"
        self.poll_s = poll_s
        self.connected = False
        self.error = ""
        self._aq = self._ae = self._lv = None
        self._state = None
        self._lock = threading.RLock()
        self._on_change: Callable[[], None] = lambda: None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._key_queue: "queue.Queue[str]" = queue.Queue()
        self._key_thread: Optional[threading.Thread] = None

    @property
    def transmit_lvar(self) -> str:
        return f"INI_RMP{self.rmp_number}_TRANSMIT_CHANNEL"

    # --- lifecycle -----------------------------------------------------------------

    def start(self, state, lock, on_change) -> None:
        self._state, self._lock, self._on_change = state, lock, on_change
        self._thread = threading.Thread(target=self._loop, name="RmpMsfs", daemon=True)
        self._thread.start()
        self._key_thread = threading.Thread(target=self._key_loop, name="RmpKeys", daemon=True)
        self._key_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sm is not None:
            try:
                self._sm.exit()
            except Exception:
                pass
            self._sm = None

    def connect(self) -> bool:
        try:
            self._aq, self._ae, self._lv = self._factory()
            self.connected, self.error = True, ""
        except Exception as e:  # sim not running, etc.
            self.connected, self.error = False, str(e)
        return self.connected

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.connected and not self.connect():
                self._stop.wait(5.0)
                continue
            try:
                self.poll()
            except Exception as e:
                self.connected, self.error = False, str(e)
            self._stop.wait(self.poll_s)

    # --- sim -> panel ----------------------------------------------------------------

    def poll(self) -> None:
        """Read the sim's radios and the A350's TCAS/XPDR/transmit LVars into the panel state."""
        aq, s = self._aq, self._state
        reads = {}
        for i in (1, 2, 3):
            reads[("act", i)] = aq.get(f"COM_ACTIVE_FREQUENCY:{i}")
            reads[("stby", i)] = aq.get(f"COM_STANDBY_FREQUENCY:{i}")
        nav = {i: aq.get(f"NAV_ACTIVE_FREQUENCY:{i}") for i in (1, 2, self.ls_nav)}
        obs = {i: aq.get(f"NAV_OBS:{i}") for i in (1, 2)}
        code = aq.get("TRANSPONDER_CODE:1")
        xpdr_state = aq.get("TRANSPONDER_STATE:1")
        lv = {}
        if self._lv is not None:
            for name in (TCAS_LVAR, TRAFFIC_LVAR, XPDR_SYS_LVAR, self.transmit_lvar):
                lv[name] = self._lv.get(name)

        changed = False

        def update(obj, attr, value):
            nonlocal changed
            if value is not None and getattr(obj, attr) != value:
                setattr(obj, attr, value)
                changed = True

        with self._lock:
            for i in (1, 2, 3):
                for kind, attr in (("act", "active"), ("stby", "standby")):
                    v = reads[(kind, i)]
                    update(s.vhf[i - 1], attr, mhz_to_channel(float(v)) if v else None)
            for i in (1, 2):
                update(s.vor[i - 1], "freq", f"{float(nav[i]):.2f}" if nav.get(i) else None)
                update(s.vor[i - 1], "crs", int(round(float(obs[i]))) % 360 if obs.get(i) is not None else None)
            update(s.ls, "freq", f"{float(nav[self.ls_nav]):.2f}" if nav.get(self.ls_nav) else None)
            update(s, "squawk", bcd_to_squawk(code) if code is not None else None)
            update(s, "tcas", tcas_from_sim(lv.get(TCAS_LVAR)))
            if xpdr_state is not None:
                st = int(round(float(xpdr_state)))
                if st in (0, 1, 2):                      # off / standby / test
                    update(s, "atc_mode", "STBY")
                elif st in (3, 4):                       # on (mode A) / alt (mode C)
                    update(s, "atc_mode", "AUTO")
                    update(s, "alt_rptg", st == 4)
                elif st == 5:                            # GND: AUTO on the ground (ALT RPTG left as set)
                    update(s, "atc_mode", "AUTO")
            update(s, "traffic", _reverse(TRAFFIC_VALUES, lv.get(TRAFFIC_LVAR)))
            sys_no = _reverse({1: 1, 2: 2}, lv.get(XPDR_SYS_LVAR))
            update(s, "xpdr_sys", sys_no)
            tx = _reverse({1: 1, 2: 2, 3: 3}, lv.get(self.transmit_lvar))  # channels 4-9 (HF etc.) leave tx alone
            update(s, "tx", tx)
        if changed:
            self._on_change()

    # --- panel -> sim ----------------------------------------------------------------

    def _fail(self, what: str, e: Exception) -> None:
        self.connected, self.error = False, f"{what}: {e}"

    def _event(self, name: str, value: Optional[int] = None) -> None:
        if not self.connected:
            return
        try:
            evt = self._ae.find(name)
            evt(value) if value is not None else evt()
        except Exception as e:
            self._fail(name, e)

    def _lvar(self, name: str, value) -> None:
        if not self.connected or self._lv is None:
            return
        try:
            self._lv.set(name, value)
        except Exception as e:
            self._fail(f"L:{name}", e)

    def _press(self, *keys: str) -> None:
        """Queue aircraft RMP key presses (RMP_<panel>_<key>_KEY); sent with gaps on the key thread."""
        for key in keys:
            self._key_queue.put(f"RMP_{self.key_panel}_{key}_KEY")

    def _key_loop(self) -> None:
        while not self._stop.is_set():
            try:
                lvar = self._key_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self._lvar(lvar, 1)
            time.sleep(KEY_PRESS_GAP_S)

    def key_sequence_for_standby(self, index: int, freq: str):
        keys = ["VHF", f"RSK_{index}"] + [f"NUM_{d}" for d in freq.replace(".", "")]
        if self.keys_confirm:
            keys.append(f"RSK_{index}")
        return keys

    def set_com_standby(self, index: int, freq: str) -> None:
        if self.freq_input == "keys":
            self._press(*self.key_sequence_for_standby(index, freq))
        else:
            self._event(COM_STBY_SET[index - 1], com_to_hz(freq))

    def swap_com(self, index: int) -> None:
        if self.freq_input == "keys":
            self._press("VHF", f"LSK_{index}")
        else:
            self._event(COM_SWAP[index - 1])

    def receive_lvar(self, index: int) -> str:
        return f"INI_RMP{self.rmp_number}_{index}_RECEIVE_SEL"

    def set_tx(self, index: int) -> None:
        """Select the transmit radio, and switch on reception for it (pulling its volume knob)."""
        self._lvar(self.receive_lvar(index), 1)
        self._lvar(self.transmit_lvar, index)

    def set_nav(self, which: str, index: int, freq: str) -> None:
        nav = self.ls_nav if which == "LS" else index
        self._event(f"NAV{nav}_RADIO_SET_HZ", nav_to_hz(freq))

    def set_crs(self, which: str, index: int, crs: int) -> None:
        """Panel-only: the A350 ignores VOR1/2_SET and NAV OBS writes (live-tested); no course LVar is listed."""

    def set_adf(self, index: int, khz: str) -> None:
        self._event(ADF_SET[index - 1], bcd(int(round(float(khz) * 1000))))

    def set_squawk(self, code: str) -> None:
        self._event("XPNDR_SET", int(code, 16))

    def ident(self) -> None:
        self._event("XPNDR_IDENT_ON")

    def can(self, what: str) -> bool:
        """Whether the panel may change this item. ATC MODE / ALT RPTG are read-only through MSFS
        (live-tested): the panel shows the aircraft's state and asks for the change in the cockpit."""
        return what not in ("xpdr_state", "vor_freq", "adf_freq")

    def set_xpdr_state(self, state: str) -> None:
        """Not possible through MSFS for the A350 (see can())."""

    def set_xpdr_sys(self, sys_no: int) -> None:
        self._lvar(XPDR_SYS_LVAR, sys_no)

    def set_tcas(self, mode: str) -> None:
        self._lvar(TCAS_LVAR, TCAS_VALUES[mode])

    def set_traffic(self, mode: str) -> None:
        self._lvar(TRAFFIC_LVAR, TRAFFIC_VALUES[mode])
