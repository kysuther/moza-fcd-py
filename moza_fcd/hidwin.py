"""Minimal Windows HID access through hid.dll / setupapi.dll (ctypes, no dependencies).

Reports are decoded with Windows' own HID parser (HidP_GetUsages / HidP_GetUsageValue),
so no report layout has to be guessed - button N is HID button usage N, exactly what
MOZA Cockpit and the Windows game-controller panel show.
"""

from __future__ import annotations

import ctypes as C
import sys
import threading
from ctypes import wintypes as W
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("moza_fcd.hidwin is Windows-only")

_hid = C.WinDLL("hid.dll")
_setup = C.WinDLL("setupapi.dll")
_k32 = C.WinDLL("kernel32.dll", use_last_error=True)

INVALID_HANDLE_VALUE = C.c_void_p(-1).value
GENERIC_READ = 0x80000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 0x1, 0x2
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x2, 0x10
ERROR_IO_PENDING = 997
WAIT_OBJECT_0, WAIT_TIMEOUT = 0, 0x102
HIDP_INPUT = 0
HIDP_STATUS_SUCCESS = 0x00110000

PAGE_GENERIC_DESKTOP = 0x01
PAGE_BUTTON = 0x09
AXIS_USAGES = {"X": 0x30, "Y": 0x31, "Z": 0x32, "Rx": 0x33, "Ry": 0x34, "Rz": 0x35, "Slider": 0x36, "Dial": 0x37}


class GUID(C.Structure):
    _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD), ("Data4", C.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("InterfaceClassGuid", GUID), ("Flags", W.DWORD), ("Reserved", C.c_void_p)]


class HIDD_ATTRIBUTES(C.Structure):
    _fields_ = [("Size", W.ULONG), ("VendorID", W.USHORT), ("ProductID", W.USHORT), ("VersionNumber", W.USHORT)]


class HIDP_CAPS(C.Structure):
    _fields_ = [("Usage", W.USHORT), ("UsagePage", W.USHORT), ("InputReportByteLength", W.USHORT),
                ("OutputReportByteLength", W.USHORT), ("FeatureReportByteLength", W.USHORT),
                ("Reserved", W.USHORT * 17), ("NumberLinkCollectionNodes", W.USHORT),
                ("NumberInputButtonCaps", W.USHORT), ("NumberInputValueCaps", W.USHORT),
                ("NumberInputDataIndices", W.USHORT), ("NumberOutputButtonCaps", W.USHORT),
                ("NumberOutputValueCaps", W.USHORT), ("NumberOutputDataIndices", W.USHORT),
                ("NumberFeatureButtonCaps", W.USHORT), ("NumberFeatureValueCaps", W.USHORT),
                ("NumberFeatureDataIndices", W.USHORT)]


class OVERLAPPED(C.Structure):
    _fields_ = [("Internal", C.c_void_p), ("InternalHigh", C.c_void_p), ("Offset", W.DWORD),
                ("OffsetHigh", W.DWORD), ("hEvent", W.HANDLE)]


def _sig(fn, restype, *argtypes):
    fn.restype, fn.argtypes = restype, list(argtypes)
    return fn


_sig(_hid.HidD_GetHidGuid, None, C.POINTER(GUID))
_sig(_hid.HidD_GetAttributes, W.BOOLEAN, W.HANDLE, C.POINTER(HIDD_ATTRIBUTES))
_sig(_hid.HidD_GetPreparsedData, W.BOOLEAN, W.HANDLE, C.POINTER(C.c_void_p))
_sig(_hid.HidD_FreePreparsedData, W.BOOLEAN, C.c_void_p)
_sig(_hid.HidD_GetProductString, W.BOOLEAN, W.HANDLE, C.c_void_p, W.ULONG)
_sig(_hid.HidP_GetCaps, C.c_long, C.c_void_p, C.POINTER(HIDP_CAPS))
_sig(_hid.HidP_MaxUsageListLength, W.ULONG, C.c_int, W.USHORT, C.c_void_p)
_sig(_hid.HidP_GetUsages, C.c_long, C.c_int, W.USHORT, W.USHORT, C.POINTER(W.USHORT), C.POINTER(W.ULONG),
     C.c_void_p, C.c_void_p, W.ULONG)
_sig(_hid.HidP_GetUsageValue, C.c_long, C.c_int, W.USHORT, W.USHORT, W.USHORT, C.POINTER(W.ULONG),
     C.c_void_p, C.c_void_p, W.ULONG)
_sig(_setup.SetupDiGetClassDevsW, W.HANDLE, C.POINTER(GUID), W.LPCWSTR, W.HWND, W.DWORD)
_sig(_setup.SetupDiEnumDeviceInterfaces, W.BOOL, W.HANDLE, C.c_void_p, C.POINTER(GUID), W.DWORD,
     C.POINTER(SP_DEVICE_INTERFACE_DATA))
_sig(_setup.SetupDiGetDeviceInterfaceDetailW, W.BOOL, W.HANDLE, C.POINTER(SP_DEVICE_INTERFACE_DATA),
     C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p)
_sig(_setup.SetupDiDestroyDeviceInfoList, W.BOOL, W.HANDLE)
_sig(_k32.CreateFileW, W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE)
_sig(_k32.CloseHandle, W.BOOL, W.HANDLE)
_sig(_k32.CreateEventW, W.HANDLE, C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR)
_sig(_k32.ReadFile, W.BOOL, W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.POINTER(OVERLAPPED))
_sig(_k32.WaitForSingleObject, W.DWORD, W.HANDLE, W.DWORD)
_sig(_k32.GetOverlappedResult, W.BOOL, W.HANDLE, C.POINTER(OVERLAPPED), C.POINTER(W.DWORD), W.BOOL)
_sig(_k32.CancelIoEx, W.BOOL, W.HANDLE, C.POINTER(OVERLAPPED))
_sig(_k32.ResetEvent, W.BOOL, W.HANDLE)


@dataclass(frozen=True)
class HidDeviceInfo:
    path: str
    vid: int
    pid: int
    usage_page: int
    usage: int
    product: str
    input_report_length: int

    @property
    def is_game_controller(self) -> bool:
        return self.usage_page == PAGE_GENERIC_DESKTOP and self.usage in (0x04, 0x05)  # joystick / gamepad

    def __str__(self) -> str:
        return (f"{self.vid:04X}:{self.pid:04X} usage={self.usage_page:#06x}/{self.usage:#04x} "
                f"in={self.input_report_length}B {self.product!r}")


def _open(path: str, access: int, flags: int = 0) -> Optional[int]:
    h = _k32.CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, flags, None)
    return None if h in (None, INVALID_HANDLE_VALUE) else h


def _interface_paths() -> List[str]:
    guid = GUID()
    _hid.HidD_GetHidGuid(C.byref(guid))
    hdev = _setup.SetupDiGetClassDevsW(C.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hdev in (None, INVALID_HANDLE_VALUE):
        return []
    paths = []
    try:
        index = 0
        while True:
            ifd = SP_DEVICE_INTERFACE_DATA()
            ifd.cbSize = C.sizeof(ifd)
            if not _setup.SetupDiEnumDeviceInterfaces(hdev, None, C.byref(guid), index, C.byref(ifd)):
                break
            index += 1
            needed = W.DWORD()
            _setup.SetupDiGetDeviceInterfaceDetailW(hdev, C.byref(ifd), None, 0, C.byref(needed), None)
            buf = C.create_string_buffer(needed.value)
            # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize: 8 on 64-bit, 6 on 32-bit.
            C.cast(buf, C.POINTER(W.DWORD))[0] = 8 if C.sizeof(C.c_void_p) == 8 else 6
            if _setup.SetupDiGetDeviceInterfaceDetailW(hdev, C.byref(ifd), buf, needed, None, None):
                paths.append(C.wstring_at(C.addressof(buf) + 4))
    finally:
        _setup.SetupDiDestroyDeviceInfoList(hdev)
    return paths


def enumerate_devices(vid: Optional[int] = None, pids: Optional[Iterable[int]] = None) -> List[HidDeviceInfo]:
    """HID interfaces currently present, optionally filtered by vendor/product IDs."""
    pid_set = set(pids) if pids is not None else None
    out = []
    for path in _interface_paths():
        h = _open(path, 0)  # zero access is enough for attributes and capabilities
        if h is None:
            continue
        try:
            attrs = HIDD_ATTRIBUTES()
            attrs.Size = C.sizeof(attrs)
            if not _hid.HidD_GetAttributes(h, C.byref(attrs)):
                continue
            if vid is not None and attrs.VendorID != vid:
                continue
            if pid_set is not None and attrs.ProductID not in pid_set:
                continue
            pp = C.c_void_p()
            caps = HIDP_CAPS()
            if _hid.HidD_GetPreparsedData(h, C.byref(pp)):
                _hid.HidP_GetCaps(pp, C.byref(caps))
                _hid.HidD_FreePreparsedData(pp)
            name = C.create_unicode_buffer(128)
            product = name.value if _hid.HidD_GetProductString(h, name, C.sizeof(name)) else ""
            out.append(HidDeviceInfo(path, attrs.VendorID, attrs.ProductID, caps.UsagePage, caps.Usage,
                                     product, caps.InputReportByteLength))
        finally:
            _k32.CloseHandle(h)
    return out


class HidInputReader:
    """Reads input reports on a thread; calls on_state(pressed_buttons, axes) for every report.

    pressed_buttons is a frozenset of HID button usages (1-based); axes maps names
    ("Z", "Rx", ...) to raw values for the axes the device actually has.
    """

    def __init__(self, info: HidDeviceInfo,
                 on_state: Callable[[FrozenSet[int], Dict[str, int]], None],
                 on_error: Callable[[str], None] = lambda m: None) -> None:
        self.info = info
        self.on_state = on_state
        self.on_error = on_error
        self._h = None
        self._pp = C.c_void_p()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._axes: Dict[str, int] = {}

    def start(self) -> "HidInputReader":
        h = _open(self.info.path, GENERIC_READ, FILE_FLAG_OVERLAPPED)
        if h is None:
            raise OSError(C.get_last_error(), f"Could not open HID device {self.info}")
        if not _hid.HidD_GetPreparsedData(h, C.byref(self._pp)):
            _k32.CloseHandle(h)
            raise OSError("HidD_GetPreparsedData failed")
        self._h = h
        listed = _hid.HidP_MaxUsageListLength(HIDP_INPUT, PAGE_BUTTON, self._pp)
        self._has_buttons = listed > 0
        self._max_usages = max(1, listed)
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="FcdHidReader", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(1.0)
        if self._pp:
            _hid.HidD_FreePreparsedData(self._pp)
            self._pp = C.c_void_p()
        if self._h:
            _k32.CloseHandle(self._h)
            self._h = None

    def __enter__(self) -> "HidInputReader":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def decode(self, report: C.Array) -> tuple:
        n = len(report)
        if self._has_buttons:
            usages = (W.USHORT * self._max_usages)()
            count = W.ULONG(self._max_usages)
            status = _hid.HidP_GetUsages(HIDP_INPUT, PAGE_BUTTON, 0, usages, C.byref(count), self._pp, report, n)
            ok = (status & 0xFFFFFFFF) == HIDP_STATUS_SUCCESS
            pressed = frozenset(usages[i] for i in range(count.value)) if ok else None
        else:
            pressed = frozenset()
        for name, usage in AXIS_USAGES.items():
            value = W.ULONG()
            st = _hid.HidP_GetUsageValue(HIDP_INPUT, PAGE_GENERIC_DESKTOP, 0, usage, C.byref(value), self._pp, report, n)
            if (st & 0xFFFFFFFF) == HIDP_STATUS_SUCCESS:
                self._axes[name] = value.value
        return pressed, dict(self._axes)

    def _loop(self) -> None:
        size = self.info.input_report_length or 64
        buf = (C.c_ubyte * size)()
        event = _k32.CreateEventW(None, True, False, None)
        ov = OVERLAPPED()
        ov.hEvent = event
        pending = False
        try:
            while self._running:
                if not pending:
                    _k32.ResetEvent(event)
                    if not _k32.ReadFile(self._h, buf, size, None, C.byref(ov)):
                        err = C.get_last_error()
                        if err != ERROR_IO_PENDING:
                            self.on_error(f"HID read failed (error {err}); device unplugged?")
                            return
                    pending = True
                if _k32.WaitForSingleObject(event, 100) != WAIT_OBJECT_0:
                    continue
                got = W.DWORD()
                pending = False
                if not _k32.GetOverlappedResult(self._h, C.byref(ov), C.byref(got), False):
                    self.on_error(f"HID read failed (error {C.get_last_error()}); device unplugged?")
                    return
                pressed, axes = self.decode(buf)
                if pressed is not None:  # None = a different report ID with no buttons
                    try:
                        self.on_state(pressed, axes)
                    except Exception as e:
                        self.on_error(f"on_state handler raised {e!r}")
        finally:
            if pending:
                _k32.CancelIoEx(self._h, C.byref(ov))
                got = W.DWORD()
                _k32.GetOverlappedResult(self._h, C.byref(ov), C.byref(got), True)
            _k32.CloseHandle(event)
