"""EXPERIMENTAL: ctypes binding to the official MOZA SDK (MOZA_SDK.dll) for the FCD's main board.

MOZA Cockpit configures the T23 CDU with the SDK's ControlPanel parameter names
(CD_CockPitSide, CD_EnableLightSensor, CD_EnableNote, CD_EnableChecklist), and drives its
status lights through a "ledId" parameter - which matches the ControlPanel command
CD_LedEnable (8064: index, on/off). This module exposes those through DeviceService.

When to use this instead of FcdScreen.set_light():
  * FcdScreen sends the same commands itself on the serial port it owns - use that while
    your app drives the screen.
  * Use this module when MOZA Cockpit/DeviceService drives the device instead. Tested on
    MOZA rudder pedals: while an SDK client has a device open, DeviceService holds its
    serial port exclusively, so the two approaches can't run at the same time.

Notes:
  * Moza_Initialize() starts MOZADeviceService.exe from the Cockpit install if it isn't
    running (found via the registry); it exits again after the last client shuts down.
  * Light IDs come from keys.LED_IDS (scanned on a real MB7F: CALL 0, FAIL 1, EXEC 2, MSG 3, OFST 4).
"""

from __future__ import annotations

import ctypes as C
import os
import sys
from typing import Dict, List, Optional, Sequence

from . import keys as K

# Device enums from moza.h
MOZA_DC_AS = 2
MOZA_DT_AS_CONTROL_PANEL = 1 << 8
MOZA_VT_INT, MOZA_VT_UINT, MOZA_VT_LIST = 2, 3, 8
MOZA_ERR_NAMES = {
    -1: "UNKNOWN", 0: "NO_ERROR", 1: "SERVICE_NOT_FOUND", 2: "SERVICE_VERSION_MISMATCH", 3: "INVALID_ARGUMENT",
    4: "NOT_INITIALIZED", 5: "ALREADY_INITIALIZED", 6: "BUFFER_TOO_SMALL", 7: "DEVICE_NOT_FOUND",
    8: "DEVICE_NOT_OPEN", 9: "INVALID_HANDLE", 10: "BUSY", 11: "TIMEOUT", 12: "NOT_SUPPORTED",
    13: "PARAMETER_READ_COMMAND_ERROR", 14: "PARAMETER_WRITE_COMMAND_ERROR", 15: "INDEX_OUT_OF_BOUNDS",
    16: "PENDING",
}


class _Str(C.Structure):
    _pack_ = 1
    _fields_ = [("data", C.c_void_p), ("size", C.c_uint32)]


class _ValueData(C.Union):
    _pack_ = 1
    _fields_ = [("intValue", C.c_int64), ("uintValue", C.c_uint64), ("doubleValue", C.c_double),
                ("boolValue", C.c_uint8), ("stringValue", _Str), ("binaryValue", _Str), ("listValue", _Str)]


class MOZA_VALUE(C.Structure):
    _pack_ = 1
    _fields_ = [("type", C.c_int), ("data", _ValueData)]


class MOZA_DEVICE_INFO(C.Structure):
    _pack_ = 1
    _fields_ = [("id", C.c_uint32), ("name", C.c_char * 64), ("portName", C.c_char * 256),
                ("vendorId", C.c_uint16), ("productId", C.c_uint16), ("category", C.c_int),
                ("type", C.c_int), ("state", C.c_int), ("hidDeviceCount", C.c_uint8), ("reserved", C.c_void_p)]


class MOZA_COMMAND_INFO(C.Structure):
    _pack_ = 1
    _fields_ = [("id", C.c_int32), ("name", C.c_char * 64), ("requestArgumentsCount", C.c_uint8),
                ("responseArgumentsCount", C.c_uint8), ("reserved", C.c_void_p)]


class MOZA_PARAMETER_INFO(C.Structure):
    _pack_ = 1
    _fields_ = [("id", C.c_int32), ("name", C.c_char * 64), ("readable", C.c_uint8), ("writable", C.c_uint8),
                ("reserved", C.c_void_p)]


class SdkError(RuntimeError):
    def __init__(self, what: str, code: int) -> None:
        super().__init__(f"{what} failed: MOZA_ERR_{MOZA_ERR_NAMES.get(code, code)}")
        self.code = code


def default_dll_path() -> Optional[str]:
    """MOZA_SDK_DLL env var, else the SDK package next to moza_fcd_py (this repo's layout)."""
    env = os.environ.get("MOZA_SDK_DLL")
    if env:
        return env
    arch = "x64" if C.sizeof(C.c_void_p) == 8 else "x86"
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.normpath(os.path.join(here, "..", "..", "MOZA_SDK", f"{arch}-release", "bin", "MOZA_SDK.dll"))
    return candidate if os.path.exists(candidate) else None


def int_value(v: int) -> MOZA_VALUE:
    value = MOZA_VALUE()
    value.type = MOZA_VT_INT
    value.data.intValue = int(v)
    return value


class MozaSdk:
    def __init__(self, dll_path: Optional[str] = None) -> None:
        if sys.platform != "win32":
            raise RuntimeError("The MOZA SDK is Windows-only.")
        path = dll_path or default_dll_path()
        if not path or not os.path.exists(path):
            raise RuntimeError("MOZA_SDK.dll not found - set MOZA_SDK_DLL to its path.")
        os.add_dll_directory(os.path.dirname(path))
        self.dll = dll = C.CDLL(path)
        H = C.c_void_p
        sig = [
            ("Moza_Initialize", C.c_int, []),
            ("Moza_Shutdown", None, []),
            ("Moza_GetSdkVersionString", C.c_char_p, []),
            ("Moza_DeviceGetList", C.c_int, [C.POINTER(MOZA_DEVICE_INFO), C.POINTER(C.c_uint32)]),
            ("Moza_DeviceOpen", C.c_int, [C.c_uint32, C.POINTER(H)]),
            ("Moza_DeviceClose", C.c_int, [H]),
            ("Moza_DeviceCommandGetInfoByName", C.c_int, [H, C.c_char_p, C.POINTER(MOZA_COMMAND_INFO)]),
            ("Moza_DeviceCommandSendSync", C.c_int, [H, C.c_int32, C.POINTER(MOZA_VALUE), C.c_int32, C.POINTER(MOZA_VALUE)]),
            ("Moza_DeviceCommandGetList", C.c_int, [H, C.POINTER(MOZA_COMMAND_INFO), C.POINTER(C.c_uint32)]),
            ("Moza_DeviceParameterGetIdByName", C.c_int, [H, C.c_char_p, C.POINTER(C.c_int32)]),
            ("Moza_DeviceParameterSetValueSync", C.c_int, [H, C.c_int32, C.POINTER(MOZA_VALUE), C.c_int32, C.POINTER(MOZA_VALUE)]),
            ("Moza_DeviceParameterGetList", C.c_int, [H, C.POINTER(MOZA_PARAMETER_INFO), C.POINTER(C.c_uint32)]),
            ("Moza_ValueDestroy", None, [C.POINTER(MOZA_VALUE)]),
        ]
        for name, restype, argtypes in sig:
            fn = getattr(dll, name)
            fn.restype, fn.argtypes = restype, argtypes
        self._initialized = False

    # --- lifecycle -----------------------------------------------------------------

    @property
    def version(self) -> str:
        return self.dll.Moza_GetSdkVersionString().decode()

    def initialize(self) -> "MozaSdk":
        self._check("Moza_Initialize", self.dll.Moza_Initialize())
        self._initialized = True
        return self

    def shutdown(self) -> None:
        if self._initialized:
            self.dll.Moza_Shutdown()
            self._initialized = False

    def __enter__(self) -> "MozaSdk":
        return self.initialize()

    def __exit__(self, *exc) -> None:
        self.shutdown()

    @staticmethod
    def _check(what: str, code: int) -> None:
        if code != 0:
            raise SdkError(what, code)

    # --- devices -------------------------------------------------------------------

    def devices(self) -> List[MOZA_DEVICE_INFO]:
        count = C.c_uint32(0)
        code = self.dll.Moza_DeviceGetList(None, C.byref(count))
        if code not in (0, 6):  # 6 = BUFFER_TOO_SMALL, expected when probing the size
            self._check("Moza_DeviceGetList", code)
        if count.value == 0:
            return []
        infos = (MOZA_DEVICE_INFO * count.value)()
        self._check("Moza_DeviceGetList", self.dll.Moza_DeviceGetList(infos, C.byref(count)))
        return list(infos[: count.value])

    def open(self, device_id: int) -> "SdkDevice":
        handle = C.c_void_p()
        self._check("Moza_DeviceOpen", self.dll.Moza_DeviceOpen(device_id, C.byref(handle)))
        return SdkDevice(self, handle)


class SdkDevice:
    def __init__(self, sdk: MozaSdk, handle: C.c_void_p) -> None:
        self.sdk, self.handle = sdk, handle

    def close(self) -> None:
        if self.handle:
            self.sdk.dll.Moza_DeviceClose(self.handle)
            self.handle = None

    def commands(self) -> List[MOZA_COMMAND_INFO]:
        return self._list(self.sdk.dll.Moza_DeviceCommandGetList, MOZA_COMMAND_INFO)

    def parameters(self) -> List[MOZA_PARAMETER_INFO]:
        return self._list(self.sdk.dll.Moza_DeviceParameterGetList, MOZA_PARAMETER_INFO)

    def _list(self, fn, struct):
        count = C.c_uint32(0)
        fn(self.handle, None, C.byref(count))
        if count.value == 0:
            return []
        arr = (struct * count.value)()
        MozaSdk._check(fn.__name__, fn(self.handle, arr, C.byref(count)))
        return list(arr[: count.value])

    def send_command(self, name: str, args: Sequence[int], timeout_ms: int = 300) -> None:
        info = MOZA_COMMAND_INFO()
        MozaSdk._check(f"CommandGetInfoByName({name})",
                       self.sdk.dll.Moza_DeviceCommandGetInfoByName(self.handle, name.encode(), C.byref(info)))
        items = (MOZA_VALUE * len(args))(*[int_value(a) for a in args])
        request = MOZA_VALUE()
        request.type = MOZA_VT_LIST
        request.data.listValue.data = C.cast(items, C.c_void_p)
        request.data.listValue.size = len(args)
        result = MOZA_VALUE()
        code = self.sdk.dll.Moza_DeviceCommandSendSync(self.handle, info.id, C.byref(request), timeout_ms, C.byref(result))
        self.sdk.dll.Moza_ValueDestroy(C.byref(result))
        MozaSdk._check(f"CommandSendSync({name})", code)

    def set_parameter(self, name: str, value: int, timeout_ms: int = 300) -> None:
        pid = C.c_int32()
        MozaSdk._check(f"ParameterGetIdByName({name})",
                       self.sdk.dll.Moza_DeviceParameterGetIdByName(self.handle, name.encode(), C.byref(pid)))
        result = MOZA_VALUE()
        code = self.sdk.dll.Moza_DeviceParameterSetValueSync(self.handle, pid.value, C.byref(int_value(value)),
                                                             timeout_ms, C.byref(result))
        self.sdk.dll.Moza_ValueDestroy(C.byref(result))
        MozaSdk._check(f"ParameterSetValueSync({name})", code)


def find_fcd_control_panel(sdk: MozaSdk) -> Optional[MOZA_DEVICE_INFO]:
    """The FCD's main board as DeviceService reports it: an AS control panel, preferring T23/CDU names."""
    panels = [d for d in sdk.devices() if d.category == MOZA_DC_AS and d.type == MOZA_DT_AS_CONTROL_PANEL]
    for d in panels:
        name = d.name.decode(errors="replace").upper()
        if "T23" in name or "CDU" in name or "FCD" in name:
            return d
    return panels[0] if panels else None


class SdkLights:
    """Light backend for FcdScreen(light_backend=...): set(name, on) via CD_LedEnable."""

    def __init__(self, led_ids: Optional[Dict[str, int]] = None, dll_path: Optional[str] = None) -> None:
        self.led_ids = dict(led_ids or K.LED_IDS)
        self.sdk = MozaSdk(dll_path).initialize()
        info = find_fcd_control_panel(self.sdk)
        if info is None:
            self.sdk.shutdown()
            raise RuntimeError("DeviceService reports no AS control panel - is the FCD connected?")
        self.device_name = info.name.decode(errors="replace")
        self.device = self.sdk.open(info.id)

    def set(self, name: str, on: bool) -> bool:
        led = self.led_ids.get(name)
        if led is None:
            return False
        self.device.send_command("CD_LedEnable", [led, 1 if on else 0])
        return True

    def set_raw(self, led_id: int, on: bool) -> None:
        self.device.send_command("CD_LedEnable", [led_id, 1 if on else 0])

    def close(self) -> None:
        self.device.close()
        self.sdk.shutdown()
