"""Real-hardware transport: find the FCD's CDC serial port and drive a ScreenSession on a thread."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

from . import keys as K
from . import protocol as p
from .page import Page
from .session import ScreenSession, SessionState

try:
    import serial  # pyserial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - only needed for real hardware
    serial = None
    list_ports = None


def _require_pyserial() -> None:
    if serial is None:
        raise RuntimeError("pyserial is required for hardware access: pip install pyserial")


@dataclass(frozen=True)
class PortInfo:
    device: str
    vid: Optional[int]
    pid: Optional[int]
    description: str
    serial_number: Optional[str]

    def __str__(self) -> str:
        ids = f"{self.vid:04X}:{self.pid:04X}" if self.vid is not None and self.pid is not None else "????:????"
        return f"{self.device:8s} {ids}  {self.description}  (SN {self.serial_number or '-'})"


def list_moza_ports() -> List[PortInfo]:
    """Every serial port with MOZA's vendor ID - wheelbases, dashes, and the FCD alike."""
    _require_pyserial()
    return [PortInfo(i.device, i.vid, i.pid, i.description or "", i.serial_number)
            for i in list_ports.comports() if i.vid == p.VENDOR_ID]


def find_fcd_ports(product_ids: Iterable[int] = p.KNOWN_FCD_PRODUCT_IDS) -> List[PortInfo]:
    pids = set(product_ids)
    return [port for port in list_moza_ports() if port.pid in pids]


class FcdScreen:
    """Owns the serial port and a background pump thread.

    Usage:
        with FcdScreen() as screen:          # or FcdScreen(port="COM7"), or product_ids=[0x....]
            screen.wait_ready()
            screen.show(Page().center(6, "HELLO", "green"))

    Note: MOZA Cockpit holds this port while it runs - close it first.
    """

    READ_TIMEOUT_S = 0.02

    def __init__(self, port: Optional[str] = None, product_ids: Iterable[int] = p.KNOWN_FCD_PRODUCT_IDS,
                 keyframe_interval_ms: int = 750, auto_refresh: bool = True,
                 on_trace: Optional[Callable[[str], None]] = None,
                 on_error: Optional[Callable[[str], None]] = None,
                 light_backend=None) -> None:
        self.port_name = port
        self.light_backend = light_backend  # optional object with set(name, on) -> bool
        self.product_ids = tuple(product_ids)
        self.keyframe_interval_s = keyframe_interval_ms / 1000.0
        self.auto_refresh = auto_refresh
        self.on_trace = on_trace or (lambda m: None)
        self.on_error = on_error or (lambda m: None)
        self.on_setting: Callable[[int, bytes], None] = lambda sid, data: None
        self.on_cabin_position: Callable[[int], None] = lambda pos: None
        # on_key(name, pressed) - names from moza_fcd.keys. Fed by the sim GUI, or on hardware by
        # buttons.FcdKeyReader(on_key=screen.emit_key) (`python -m moza_fcd run` does this for you).
        self.on_key: Callable[[str, bool], None] = lambda name, pressed: None
        # Ambient light sensors (HID axes LIGHT_SENSOR_A/B/AB, raw 0-65535; 0 while the device's
        # photoresistor switch is off). Latest values in light_levels; fed by emit_axis.
        self.light_levels: Dict[str, int] = {}
        self.on_light: Callable[[str, int], None] = lambda name, value: None
        self.on_device_log: Callable[[str], None] = lambda text: None  # firmware debug log lines
        self._reply_waiters = []
        self._reply_lock = threading.Lock()

        self._keyframe_interval_ms = keyframe_interval_ms
        self._serial = None
        self._session: Optional[ScreenSession] = None
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._ready = threading.Event()
        self._last_page: Optional[Page] = None
        self._next_refresh = float("inf")

    # --- lifecycle -----------------------------------------------------------------

    def connect(self) -> "FcdScreen":
        if self._serial is not None:
            return self
        name = self.port_name
        if name == "sim":
            from .sim import SimSerial
            ser = SimSerial(timeout=self.READ_TIMEOUT_S)
        else:
            ser = self._open_serial(name)

        # The device keeps transmitting with nobody listening; stale bytes would be
        # mistaken for the handshake reply.
        ser.reset_input_buffer()

        session = ScreenSession(self._write, self._keyframe_interval_ms)
        session.on_trace = lambda m: self.on_trace(m)
        session.on_error = lambda m: self.on_error(m)
        session.on_setting = lambda sid, data: self.on_setting(sid, data)
        session.on_cabin_position = lambda pos: self.on_cabin_position(pos)
        session.on_ready = self._ready.set
        session.on_device_reply = self._on_device_reply
        session.on_device_log = lambda text: self.on_device_log(text)

        self._serial, self._session = ser, session
        self._running = True
        self._thread = threading.Thread(target=self._pump, name=f"FcdScreen-{self.port_name}", daemon=True)
        self._thread.start()
        return self

    @property
    def simulator(self):
        """The SimulatedFcd when connected with port="sim", else None."""
        return getattr(self._serial, "device", None)

    def _open_serial(self, name: Optional[str]):
        _require_pyserial()
        if name is None:
            ports = find_fcd_ports(self.product_ids)
            if not ports:
                pids = ", ".join(f"0x{x:04X}" for x in self.product_ids)
                raise RuntimeError(f"No FCD serial port found for VID 0x{p.VENDOR_ID:04X} PID(s) {pids}. "
                                   "Run `python -m moza_fcd list` to see MOZA ports, then pass port= or product_ids=.")
            name = ports[0].device
        self.port_name = name

        # DTR must be asserted or the CDC device doesn't consider a host present.
        ser = serial.Serial()
        ser.port = name
        ser.baudrate = p.BAUD_RATE
        ser.timeout = self.READ_TIMEOUT_S
        ser.write_timeout = 1.0
        ser.dtr = True
        ser.open()
        return ser

    def close(self, timeout: float = 5.0) -> None:
        if self._serial is None:
            return
        with self._lock:
            self._session.begin_shutdown(time.monotonic())
        deadline = time.monotonic() + timeout
        while self._thread.is_alive() and time.monotonic() < deadline:
            with self._lock:
                if self._session.shutdown_complete:
                    break
            time.sleep(0.05)
        self._running = False
        self._thread.join(1.0)
        try:
            self._serial.close()
        except Exception:
            pass
        self._serial = None
        self._session = None
        self._ready.clear()

    def __enter__(self) -> "FcdScreen":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    # --- application API -----------------------------------------------------------

    @property
    def state(self) -> SessionState:
        return self._session.state if self._session else SessionState.CLOSED

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def wait_ready(self, timeout: float = 15.0) -> bool:
        """Block until the device is in MCDU mode (handshake + settings window take ~4-5 s)."""
        return self._ready.wait(timeout)

    def show(self, page: Page) -> None:
        """Display a page. Safe to call before ready - the latest page is sent once ready."""
        with self._lock:
            if self._session is None:
                raise RuntimeError("Not connected.")
            self._last_page = page.copy()
            self._session.submit_page(page)
            self._next_refresh = time.monotonic() + self.keyframe_interval_s

    def show_winwing_json(self, data) -> None:
        self.show(Page.from_winwing_json(data))

    def emit_key(self, name: str, pressed: bool = True) -> None:
        """Deliver a key event to on_key. Callbacks run on the caller's thread - keep them quick."""
        try:
            self.on_key(name, pressed)
        except Exception as e:
            self.on_error(f"on_key handler raised {e!r}")

    def emit_axis(self, name: str, value: int) -> None:
        """Deliver a light-sensor reading (from buttons.FcdKeyReader's on_axis)."""
        self.light_levels[name] = value
        try:
            self.on_light(name, value)
        except Exception as e:
            self.on_error(f"on_light handler raised {e!r}")

    # --- main-board controls (lights, brightness, settings) ------------------------------
    #
    # These go out as ControlPanel commands on the same serial port as the screen (encoding
    # from the SDK's own command library; see protocol.CMD_*). Untested on hardware.

    def send_device_command(self, cmd_ids, args=()) -> bool:
        with self._lock:
            return bool(self._session and self._session.send_device_command(cmd_ids, args))

    def set_light(self, name: str, on: bool) -> bool:
        """Set a status light: EXEC, MSG, FAIL, CALL or OFST (see keys.LIGHTS).

        Uses light_backend if one was given (e.g. sdk.SdkLights), otherwise CD_LedEnable
        with the index from keys.LED_IDS (scanned on a real MB7F). Returns False before the
        session is running.
        """
        if self.light_backend is not None:
            return self.light_backend.set(name, on)
        led = K.LED_IDS.get(name)
        return led is not None and self.set_light_raw(led, on)

    def set_light_raw(self, index: int, on: bool) -> bool:
        return self.send_device_command(p.CMD_LED_ENABLE, (index, 1 if on else 0))

    def set_brightness(self, value: int, group: int = 0) -> bool:
        """Telemetry/device-mode brightness (0-255) of a lighting group. No visible effect while the group
        is in manual mode (the default) - use set_key_backlight / set_indicator_brightness /
        set_screen_brightness for the brightness controls MOZA Cockpit uses."""
        return self.send_device_command(p.CMD_SET_BRIGHTNESS + (group,), (max(0, min(255, value)),))

    # --- brightness (as MOZA Cockpit's sliders do it; all persist on the device) -----------

    def set_screen_brightness(self, percent: int) -> bool:
        """Screen brightness 0-100 % (screen-board setting 0x01)."""
        return self.request_setting(p.SETTING_SCREEN_BRIGHTNESS, p.u32le(max(0, min(100, int(percent)))))

    def screen_brightness(self) -> Optional[int]:
        """Last screen brightness the device reported (percent), or None before settings arrive."""
        data = self.settings.get(p.SETTING_SCREEN_BRIGHTNESS)
        return int.from_bytes(data[:4], "little") if data and len(data) >= 4 else None

    def _set_group_level(self, group: int, level: int) -> bool:
        level = max(0, min(15, int(level)))
        ok = self.send_device_command(p.CMD_SET_INDICATOR_MODE + (group,), (0,))   # manual mode first
        return ok and self.send_device_command(p.CMD_SET_BRIGHTNESS_RANGE + (group,), (level,))

    def set_key_backlight(self, level: int) -> bool:
        """Key backlight 0-15 (main-board lighting group 1)."""
        return self._set_group_level(p.LIGHT_GROUP_KEYS, level)

    def set_indicator_brightness(self, level: int) -> bool:
        """Status-light (annunciator) brightness 0-15 (main-board lighting group 3)."""
        return self._set_group_level(p.LIGHT_GROUP_INDICATORS, level)

    def key_backlight(self, timeout: float = 1.0) -> Optional[int]:
        reply = self.query_device(p.device_read_ids(p.CMD_GET_BRIGHTNESS_RANGE, p.LIGHT_GROUP_KEYS), timeout)
        return reply[-1] if reply else None

    def indicator_brightness(self, timeout: float = 1.0) -> Optional[int]:
        reply = self.query_device(p.device_read_ids(p.CMD_GET_BRIGHTNESS_RANGE, p.LIGHT_GROUP_INDICATORS), timeout)
        return reply[-1] if reply else None

    # --- main-board reads -----------------------------------------------------------------

    def query_device(self, cmd_ids, timeout: float = 1.0) -> Optional[bytes]:
        """Send a main-board read (group 68) and wait for its reply payload ([id, group, value...]).
        Returns None on timeout. Don't call while holding the screen's lock or from on_key."""
        key = bytes(cmd_ids[1:3])
        done = threading.Event()
        box = {}

        def waiter(cmd, payload):
            if cmd == p.DEVICE_READ_REPLY and payload[:len(key)] == key and not done.is_set():
                box["payload"] = payload
                done.set()
                return True
            return False

        with self._reply_lock:
            self._reply_waiters.append(waiter)
        try:
            if not self.send_device_command(cmd_ids):
                return None
            done.wait(timeout)
            return box.get("payload")
        finally:
            with self._reply_lock:
                if waiter in self._reply_waiters:
                    self._reply_waiters.remove(waiter)

    def _on_device_reply(self, cmd: int, payload: bytes) -> None:
        with self._reply_lock:
            waiters = list(self._reply_waiters)
        for w in waiters:
            if w(cmd, payload):
                break

    def set_lighting_mode(self, mode: int, group: int = 0) -> bool:
        """0 manual, 1 follow game telemetry, 2 follow another device."""
        return self.send_device_command(p.CMD_SET_INDICATOR_MODE + (group,), (mode,))

    def set_cockpit_side(self, side: int) -> bool:
        """0 = captain (left), 1 = first officer (right)."""
        return self.send_device_command(p.CMD_COCKPIT_SIDE, (side,))

    def enable_light_sensor(self, on: bool) -> bool:
        return self.send_device_command(p.CMD_ENABLE_LIGHT_SENSOR, (1 if on else 0,))

    def set_display_mode(self, mode: int) -> bool:
        """0 = device's own UI (clock/checklists...), 1 = your content."""
        with self._lock:
            return bool(self._session and self._session.set_display_mode(mode))

    def request_setting(self, setting_id: int, data: bytes) -> bool:
        """Send a raw settings frame on port 9020 - for experimenting with undocumented settings."""
        with self._lock:
            return bool(self._session and self._session.request_setting(setting_id, data))

    @property
    def settings(self) -> dict:
        with self._lock:
            return dict(self._session.settings.settings) if self._session else {}

    @property
    def mcu_id(self) -> Optional[bytes]:
        with self._lock:
            return self._session.settings.mcu_id if self._session else None

    @property
    def supported_setting_ids(self) -> Optional[bytes]:
        with self._lock:
            return self._session.settings.supported_ids if self._session else None

    # --- internals -----------------------------------------------------------------

    def _write(self, data: bytes) -> None:
        self._serial.write(data)

    def _pump(self) -> None:
        with self._lock:
            self._session.start()
        while self._running:
            try:
                data = self._serial.read(512)
            except Exception as e:  # port unplugged/closed
                if self._running:
                    self.on_error(f"Serial read failed: {e}")
                self._running = False
                return
            now = time.monotonic()
            try:
                with self._lock:
                    if data:
                        self._session.feed(data, now)
                    self._session.tick(now)
                    self._maybe_refresh(now)
                    if self._session.shutdown_complete:
                        self._running = False
            except Exception as e:
                self.on_error(f"Session error: {e!r}")

    def _maybe_refresh(self, now: float) -> None:
        # We advertise a keyframe interval to the device, so honour it with periodic
        # full keyframes - but never pile them up behind a slow link.
        if not self.auto_refresh or self._last_page is None or now < self._next_refresh:
            return
        if self._session.mux.queued_chunks(p.PORT_MCDU_TCP) == 0:
            self._session.force_resend()
        self._next_refresh = now + self.keyframe_interval_s
