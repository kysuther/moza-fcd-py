"""A simulated FCD that speaks the device side of the protocol.

It's built from the same reverse-engineered rules as the host side, so it proves
the two halves agree - not that the real firmware behaves identically. Use it to
develop apps without hardware: `FcdScreen(port="sim")`.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, List, Optional

from . import keys as K
from . import protocol as p
from .page import COLUMNS, ROWS

ANSI = {1: "36", 2: "31", 3: "33", 4: "32", 5: "35", 6: "38;5;214", 7: "37", 8: "90"}


class _DeviceStream:
    def __init__(self, service_port: int, device_port: int, isn: int) -> None:
        self.service_port = service_port
        self.device_port = device_port
        self.syn_isn = isn
        self.host_port: Optional[int] = None
        self.established = False
        self.peer_last_isn: Optional[int] = None
        self.send_isn = (isn + 1) & 0xFFFF
        self.outbox: List[bytes] = []
        self.in_flight: Optional[int] = None


class SimulatedFcd:
    """Device side. Wire it to a host with `send` (device -> host bytes) and `feed()` (host -> device)."""

    def __init__(self, send: Callable[[bytes], None], cabin_position: int = 0, mcu_id: bytes = b"\xAA\xBB\xCC\xDD") -> None:
        self._send = send
        self._decoder = p.SerialLinkDecoder()
        self._streams: Dict[int, _DeviceStream] = {}  # by device port
        self._settings_extractor = p.SettingsFrameExtractor()
        self._mcdu_extractor = p.NetworkPackageExtractor()
        self._telemetry_extractor = p.NetworkPackageExtractor()
        self.mcu_id = mcu_id
        self.settings: Dict[int, bytes] = {p.SETTING_CABIN_POSITION: bytes([cabin_position]),
                                           p.SETTING_DISPLAY_MODE: b"\x00",
                                           p.SETTING_SCREEN_BRIGHTNESS: p.u32le(73)}
        self.text = [[" "] * COLUMNS for _ in range(ROWS)]
        self.styles = [[0x87] * COLUMNS for _ in range(ROWS)]
        self.last_sequence = 0
        self.frames_applied = 0
        self.frames_rejected = 0
        self.init_config: Optional[bytes] = None
        self.telemetry_token = b"\x01\x02\x03\x04"
        self.telemetry_token_echoed = False
        self.time_sync: Optional[bytes] = None
        self.initialized = False
        self.reply_to_fin = True
        self.lights: Dict[str, bool] = {}       # from CD_LedEnable commands, keyed by keys.LED_IDS name
        self.brightness: Dict[int, int] = {}    # lighting group -> 0-255 (telemetry/device-mode value)
        # Main-board lighting groups as a real MB7F reported them; group 2 ignores writes like the hardware.
        self.light_groups: Dict[int, Dict[str, int]] = {1: {"mode": 0, "range": 5, "bright": 255},
                                                        2: {"mode": 0, "range": 0, "bright": 0},
                                                        3: {"mode": 0, "range": 5, "bright": 255}}
        self.device_commands: List[tuple] = []  # every main-board command received

    @property
    def display_mode(self) -> int:
        return self.settings[p.SETTING_DISPLAY_MODE][0]

    # --- host -> device ------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        for msg in self._decoder.feed(data):
            self._handle(msg)

    def _handle(self, msg: p.SerialLinkMessage) -> None:
        if msg.command == 0x00 and msg.device_pair == p.DEVICE_PAIR_TO_DEVICE and not msg.payload:
            self._send(p.ROOT_HANDSHAKE_RESPONSE)
            return
        if msg.command == p.CMD_LED_ENABLE[0] and msg.device_pair == p.DEVICE_PAIR_TO_DEVICE:
            self._handle_device_command(msg)
            return
        if msg.command == p.CMD_GET_BRIGHTNESS[0] and msg.device_pair == p.DEVICE_PAIR_TO_DEVICE:
            self._handle_device_read(msg)
            return
        tunnel = p.tunnel_unwrap(msg)
        if tunnel is None:
            return
        if tunnel.inner_command == 0x00 and not tunnel.is_reply:
            self._send(p.encode_serial_frame(0xC3, p.DEVICE_PAIR_FROM_DEVICE, b"\x80"))
            if not self.initialized:
                self.initialized = True
                for i, port in enumerate((p.PORT_SETTINGS, p.PORT_MCDU_TCP, p.PORT_TELEMETRY)):
                    s = _DeviceStream(port, 0x1234 + i * 0x10, 0x2000 + i * 0x100)
                    self._streams[s.device_port] = s
                    self._raw_stream(p.pack_syn(port, p.StreamType.SYN1, s.syn_isn, s.device_port, 3))
            return
        if tunnel.inner_command != p.STREAM_INNER_COMMAND:
            return
        if tunnel.is_reply:
            self._handle_ack(p.parse_stream_ack(tunnel.inner_payload))
        else:
            self._handle_request(p.parse_stream_request(tunnel.inner_payload))

    def _handle_device_command(self, msg: p.SerialLinkMessage) -> None:
        """Main-board 'set' commands (group 69): record them, apply lights, ack with group|0x80."""
        payload = msg.payload
        self.device_commands.append((msg.command,) + tuple(payload))
        if len(payload) >= 3 and payload[0] == p.CMD_LED_ENABLE[1]:
            names = {v: k for k, v in K.LED_IDS.items()}
            self.lights[names.get(payload[1], f"LED{payload[1]}")] = bool(payload[2])
        elif len(payload) >= 3 and payload[0] == p.CMD_SET_BRIGHTNESS[1]:
            self.brightness[payload[1]] = payload[2]
        if len(payload) >= 3 and payload[1] in (1, 3):
            field = {p.CMD_SET_INDICATOR_MODE[1]: "mode", p.CMD_SET_BRIGHTNESS_RANGE[1]: "range"}.get(payload[0])
            if field:
                self.light_groups[payload[1]][field] = payload[2]
        self._send(p.encode_serial_frame(msg.command | 0x80, p.DEVICE_PAIR_FROM_DEVICE))

    def _handle_device_read(self, msg: p.SerialLinkMessage) -> None:
        """Main-board reads (group 68): reply 0xC4 [id, group, value] for lighting groups."""
        payload = msg.payload
        if len(payload) >= 2 and payload[1] in self.light_groups:
            field = {p.CMD_GET_INDICATOR_MODE[1]: "mode", p.CMD_GET_BRIGHTNESS_RANGE[1]: "range",
                     p.CMD_GET_BRIGHTNESS[1]: "bright"}.get(payload[0])
            if field:
                value = self.light_groups[payload[1]][field]
                self._send(p.encode_serial_frame(p.DEVICE_READ_REPLY, p.DEVICE_PAIR_FROM_DEVICE,
                                                 bytes([payload[0], payload[1], value])))

    @property
    def screen_brightness(self) -> int:
        data = self.settings.get(p.SETTING_SCREEN_BRIGHTNESS, b"")
        return int.from_bytes(data[:4], "little") if len(data) >= 4 else 100

    def _handle_request(self, req: p.StreamRequest) -> None:
        s = self._streams.get(req.destination_port)
        if s is None:
            return
        if req.type == p.StreamType.SYN2:
            s.host_port = req.announced_port
            s.peer_last_isn = req.isn
            self._raw_stream(p.pack_ack(s.host_port, req.isn), reply=True)
            if not s.established:
                s.established = True
                self._on_established(s)
            return
        is_new = req.isn == ((s.peer_last_isn + 1) & 0xFFFF)
        if is_new:
            s.peer_last_isn = req.isn
            if req.type == p.StreamType.TRANS and req.data:
                self._on_app_data(s, req.data)
        self._raw_stream(p.pack_ack(s.host_port, s.peer_last_isn), reply=True)
        if is_new and req.type == p.StreamType.FIN and self.reply_to_fin:
            isn = s.send_isn
            s.send_isn = (isn + 1) & 0xFFFF
            self._raw_stream(p.pack_fin(s.host_port, isn))

    def _handle_ack(self, ack: Optional[p.StreamAck]) -> None:
        if ack is None:
            return
        s = self._streams.get(ack.destination_port)
        if s is None or s.in_flight is None or ack.acknowledged_isn != s.in_flight:
            return
        s.in_flight = None
        s.outbox.pop(0)
        self._pump(s)

    # --- device -> host ------------------------------------------------------------

    def _raw_stream(self, payload: bytes, reply: bool = False) -> None:
        cb = p.STREAM_INNER_COMMAND | (0x80 if reply else 0)
        outer = 0xC3 if reply else 0x43
        self._send(p.encode_serial_frame(outer, p.DEVICE_PAIR_FROM_DEVICE, bytes([cb]) + payload))

    def _app_send(self, s: _DeviceStream, data: bytes) -> None:
        for off in range(0, len(data), p.MAX_APPLICATION_CHUNK):
            s.outbox.append(data[off : off + p.MAX_APPLICATION_CHUNK])
        self._pump(s)

    def _pump(self, s: _DeviceStream) -> None:
        if s.in_flight is not None or not s.outbox:
            return
        isn = s.send_isn
        s.send_isn = (isn + 1) & 0xFFFF
        s.in_flight = isn
        self._raw_stream(p.pack_trans(s.host_port, isn, s.outbox[0]))

    def _on_established(self, s: _DeviceStream) -> None:
        if s.service_port == p.PORT_SETTINGS:
            ids = bytes([p.SETTING_CABIN_POSITION, p.SETTING_DISPLAY_MODE])
            preamble = p.SETTINGS_PREAMBLE_HEADER + bytes([0x00, len(self.mcu_id), len(ids)]) + self.mcu_id + ids
            legacy = b"\x01" + b"\x00" * 4 + b"\x05\x00" + b"\x04" + b"\x00" * 8 + b"\x06\x00"
            framed = b"".join(p.pack_setting_frame(k, v) for k, v in self.settings.items())
            self._app_send(s, preamble + legacy + framed)
        elif s.service_port == p.PORT_TELEMETRY:
            self._app_send(s, b"\xFF" + p.pack_network_package(p.PKG_TELEMETRY_TOKEN, self.telemetry_token))

    def _on_app_data(self, s: _DeviceStream, data: bytes) -> None:
        if s.service_port == p.PORT_SETTINGS:
            for frame in self._settings_extractor.feed(data):
                if frame.setting_id == p.SETTING_TIME_SYNC:
                    self.time_sync = frame.data
                self.settings[frame.setting_id] = frame.data
                self._app_send(s, p.pack_setting_frame(frame.setting_id, frame.data))  # echo
        elif s.service_port == p.PORT_TELEMETRY:
            for pkg in self._telemetry_extractor.feed(data):
                if pkg.package_id == p.PKG_TELEMETRY_TOKEN and pkg.payload == self.telemetry_token:
                    self.telemetry_token_echoed = True
        elif s.service_port == p.PORT_MCDU_TCP:
            for pkg in self._mcdu_extractor.feed(data):
                if pkg.package_id == p.PKG_MCDU_INIT_CONFIG:
                    self.init_config = pkg.payload
                    self._app_send(s, p.pack_network_package(p.PKG_MCDU_CLIENT_CAPABILITY, bytes([2, 0, 0x33, 0x23, 0])))
                elif pkg.package_id in (p.PKG_MCDU_KEYFRAME, p.PKG_MCDU_DELTA):
                    self._apply_patch(p.parse_mcdu_patch(pkg))

    def _apply_patch(self, patch: p.McduPatch) -> None:
        if patch.sequence <= self.last_sequence:
            self.frames_rejected += 1  # only strictly increasing sequences are accepted
            return
        self.last_sequence = patch.sequence
        if patch.keyframe:
            self.text = [[" "] * COLUMNS for _ in range(ROWS)]
        for row in patch.text_rows:
            for start, text in row.segments:
                for i, ch in enumerate(text):
                    if start + i < COLUMNS:
                        self.text[row.row][start + i] = ch
        for row in patch.style_rows:
            for start, styles in row.segments:
                for i, st in enumerate(styles):
                    if start + i < COLUMNS:
                        self.styles[row.row][start + i] = st
        self.frames_applied += 1

    # --- inspection ----------------------------------------------------------------

    def screen_text(self) -> str:
        return "\n".join("".join(r) for r in self.text)

    def render_ansi(self) -> str:
        lines = ["+" + "-" * COLUMNS + "+"]
        for r in range(ROWS):
            out = []
            for c in range(COLUMNS):
                st = self.styles[r][c]
                bold = "1;" if st & 0x80 else "2;"
                out.append(f"\x1b[{bold}{ANSI.get(st & 0x0F, '37')}m{self.text[r][c]}\x1b[0m")
            lines.append("|" + "".join(out) + "|")
        lines.append("+" + "-" * COLUMNS + "+")
        return "\n".join(lines)


class SimSerial:
    """Duck-types the bits of pyserial's Serial that FcdScreen uses, backed by a SimulatedFcd."""

    def __init__(self, timeout: float = 0.02, **sim_kwargs) -> None:
        self.timeout = timeout
        self._rx = bytearray()
        self._cv = threading.Condition()
        self.device = SimulatedFcd(self._from_device, **sim_kwargs)

    def _from_device(self, data: bytes) -> None:
        with self._cv:
            self._rx.extend(data)
            self._cv.notify_all()

    def write(self, data: bytes) -> int:
        self.device.feed(data)
        return len(data)

    def read(self, size: int = 1) -> bytes:
        with self._cv:
            if not self._rx:
                self._cv.wait(self.timeout)
            out = bytes(self._rx[:size])
            del self._rx[:size]
            return out

    def reset_input_buffer(self) -> None:
        with self._cv:
            self._rx.clear()

    def close(self) -> None:
        pass
