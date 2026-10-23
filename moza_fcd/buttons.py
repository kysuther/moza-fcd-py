"""Key input from the real FCD.

The MB7F's keys are ordinary HID joystick buttons (MOZA Cockpit's key-test page numbers
them 1-71), so they're read with Windows' own HID parser and mapped to the key names in
moza_fcd.keys - the same names the sim GUI produces:

    from moza_fcd.buttons import FcdKeyReader
    reader = FcdKeyReader(on_key=screen.emit_key).start()

The keys are readable while MOZA Cockpit runs too (HID game controllers are shared).
"""

from __future__ import annotations

import sys
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

from . import keys as K
from . import protocol as p


def button_events(old: FrozenSet[int], new: FrozenSet[int], button_map: Dict[int, str]) -> List[Tuple[str, bool]]:
    """Diff two pressed-button sets into (key_name, pressed) events, releases first.
    Unmapped buttons come through as "BTN<n>"."""
    events = [(button_map.get(b, f"BTN{b}"), False) for b in sorted(old - new)]
    events += [(button_map.get(b, f"BTN{b}"), True) for b in sorted(new - old)]
    return events


def find_key_interfaces(product_ids: Optional[Iterable[int]] = p.KNOWN_FCD_PRODUCT_IDS):
    """MOZA HID game-controller interfaces, optionally limited to the given product IDs."""
    if sys.platform != "win32":
        raise RuntimeError("Key input currently uses the Windows HID API (hid.dll).")
    from . import hidwin
    return [d for d in hidwin.enumerate_devices(vid=p.VENDOR_ID, pids=product_ids) if d.is_game_controller]


class FcdKeyReader:
    """Reads the FCD's keys and light sensors.

    on_key(name, pressed)   - names from moza_fcd.keys (e.g. "LSK1L", "EXEC", "A")
    on_axis(name, value)    - LIGHT_SENSOR_A / LIGHT_SENSOR_B / LIGHT_SENSOR_AB, raw 0-65535
    """

    def __init__(self, on_key: Callable[[str, bool], None],
                 on_axis: Optional[Callable[[str, int], None]] = None,
                 product_ids: Optional[Iterable[int]] = p.KNOWN_FCD_PRODUCT_IDS,
                 button_map: Dict[int, str] = K.MB7F_HID_BUTTONS,
                 axis_names: Dict[str, str] = K.MB7F_HID_AXES,
                 interface=None,
                 on_error: Callable[[str], None] = lambda m: None) -> None:
        self.on_key = on_key
        self.on_axis = on_axis or (lambda name, value: None)
        self.product_ids = product_ids
        self.button_map = button_map
        self.axis_names = axis_names
        self.interface = interface
        self.on_error = on_error
        self._pressed: FrozenSet[int] = frozenset()
        self._axes: Dict[str, int] = {}
        self._reader = None

    def start(self) -> "FcdKeyReader":
        from . import hidwin
        iface = self.interface
        if iface is None:
            found = find_key_interfaces(self.product_ids)
            if not found:
                pids = ", ".join(f"0x{x:04X}" for x in self.product_ids or [])
                raise RuntimeError(f"No MOZA HID game controller found for PID(s) {pids or 'any'}. "
                                   "Run `python -m moza_fcd list`.")
            iface = found[0]
        self.interface = iface
        self._reader = hidwin.HidInputReader(iface, self._on_state, self.on_error).start()
        return self

    def stop(self) -> None:
        if self._reader:
            self._reader.stop()
            self._reader = None

    def __enter__(self) -> "FcdKeyReader":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def _on_state(self, pressed: FrozenSet[int], axes: Dict[str, int]) -> None:
        for name, is_down in button_events(self._pressed, pressed, self.button_map):
            self.on_key(name, is_down)
        self._pressed = pressed
        for axis, value in axes.items():
            if axis in self.axis_names and self._axes.get(axis) != value:
                self._axes[axis] = value
                self.on_axis(self.axis_names[axis], value)
