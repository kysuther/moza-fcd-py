"""Wire-level encoding for the MOZA FCD display's CDC serial protocol.

Everything in this module is pure (no I/O, no clocks), so it can be unit tested
against known-good byte sequences.

Layering, outermost first:

    SerialLink frame   7E | Len | Cmd | DevicePair | Payload | Checksum   (0x7E escaped as 7E 7E)
      0x43 tunnel      payload[0] = inner command (bit7 = inner reply flag)
        Reliable Stream  SYN1/SYN2/TRANS/FIN requests and ACKs, addressed by 16-bit "ports"
          Application    NetworkPackage (MCDU, telemetry) or settings frames

Ported from MobiFlight's MobiFlightMoza library (MIT licensed), see NOTICE.
"""

from __future__ import annotations

import enum
import struct
import zlib
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VENDOR_ID = 0x346E
# FCD PIDs (composite HID + CDC, same PID on both): 0x1305 MA3F (from MobiFlight),
# 0x1306 MB7F (seen on real hardware, firmware 1.2.7.30). Other models: `python -m moza_fcd list`.
KNOWN_FCD_PRODUCT_IDS = (0x1305, 0x1306)

BAUD_RATE = 2_000_000

START_OF_FRAME = 0x7E
CHECKSUM_SEED = 0x0D + 0x7E
MAX_SERIAL_PAYLOAD = 64

TUNNEL_COMMAND = 0x43
DEVICE_PAIR_TO_DEVICE = 0x12
DEVICE_PAIR_FROM_DEVICE = 0x21
MAX_TUNNEL_INNER_PAYLOAD = 62

STREAM_INNER_COMMAND = 0x7C
# A TRANS frame's own framing (port 2 + type 1 + ISN 2 + CRC 4) must fit in the
# tunnel's 62-byte inner payload, so application chunks max out at 53 bytes.
MAX_APPLICATION_CHUNK = 53

PORT_TELEMETRY = 9010
PORT_SETTINGS = 9020
PORT_FILE_TRANSFER = 9030
PORT_MCDU_TCP = 9050
PORT_MCDU_UDP = 9051
RESERVED_PORTS = frozenset({PORT_TELEMETRY, PORT_SETTINGS, PORT_FILE_TRANSFER, PORT_MCDU_TCP, PORT_MCDU_UDP})

MCDU_ROWS = 14
MCDU_COLUMNS = 24

# Settings IDs seen so far (port 9020).
# Screen-board settings. Names follow MOZA Cockpit's FCD settings (cabinPosition, currentFont,
# backgroundColor, textColor, displayMode, checklistEnable, clipboardEnable, displayOffset,
# displayCellSize); values seen on a real MB7F in brackets.
SETTING_SCREEN_PERCENT = 0x01   # screen brightness, u32 LE percent [73 = Cockpit 11/15]
SETTING_TIME_SYNC = 0x02
SETTING_PROTOCOL_VERSION = 0x07
SETTING_CABIN_POSITION = 0x13  # 0 = captain, 1 = first officer
SETTING_BACKGROUND_COLOR = 0x16  # ARGB? [ff 00 00 00]
SETTING_TEXT_COLOR = 0x17      # slot:i32 LE + colour (from MobiFlight tests; semantics unverified)
SETTING_DISPLAY_MODE = 0x18    # 0 = device's own UI, 1 = MCDU (host-driven grid)
SETTING_CHECKLIST_ENABLE = 0x19  # [01]
SETTING_CLIPBOARD_ENABLE = 0x1A  # [01]
SETTING_DISPLAY_OFFSET = 0x1B  # x:i32 LE + y:i32 LE [10, -2]
SETTING_CELL_SIZE = 0x1C       # width:i32 LE + height:i32 LE pixels [22, 34]

# NetworkPackage IDs.
PKG_TELEMETRY_TOKEN = 0x06
PKG_MCDU_KEYFRAME = 0x30
PKG_MCDU_DELTA = 0x31
PKG_MCDU_INIT_CONFIG = 0x32
PKG_MCDU_CLIENT_CAPABILITY = 0x33


class ProtocolError(Exception):
    pass


def crc32(data: bytes) -> int:
    """Standard CRC-32 (poly 0xEDB88320, init/xorout 0xFFFFFFFF) - same as zlib's."""
    return zlib.crc32(data) & 0xFFFFFFFF


def u16le(v: int) -> bytes:
    return struct.pack("<H", v & 0xFFFF)


def u16be(v: int) -> bytes:
    return struct.pack(">H", v & 0xFFFF)


def u32le(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


def u32be(v: int) -> bytes:
    return struct.pack(">I", v & 0xFFFFFFFF)


def i32le(v: int) -> bytes:
    return struct.pack("<i", v)


def u64le(v: int) -> bytes:
    return struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF)


# ---------------------------------------------------------------------------
# SerialLink framing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SerialLinkMessage:
    command: int
    device_pair: int
    payload: bytes


def encode_serial_frame(command: int, device_pair: int, payload: bytes = b"") -> bytes:
    """Encode a SerialLink frame. Escaped 0x7E bytes are sent twice and counted twice in the checksum."""
    payload = payload or b""
    if len(payload) > MAX_SERIAL_PAYLOAD:
        raise ValueError(f"Payload exceeds the {MAX_SERIAL_PAYLOAD}-byte protocol maximum.")

    out = bytearray([START_OF_FRAME])
    total = CHECKSUM_SEED
    for b in bytes([len(payload), command & 0xFF, device_pair & 0xFF]) + payload:
        out.append(b)
        total += b
        if b == START_OF_FRAME:
            out.append(b)
            total += b
    checksum = total & 0xFF
    out.append(checksum)
    if checksum == START_OF_FRAME:
        out.append(checksum)
    return bytes(out)


class SerialLinkDecoder:
    """Incremental SerialLink decoder; resyncs on garbage, bad lengths and bad checksums."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, data: bytes) -> List[SerialLinkMessage]:
        self._buf.extend(data)
        out: List[SerialLinkMessage] = []
        while True:
            progressed, message = self._extract_one()
            if message is not None:
                out.append(message)
            if not progressed:
                return out

    def _extract_one(self) -> Tuple[bool, Optional[SerialLinkMessage]]:
        buf = self._buf
        sof = buf.find(START_OF_FRAME)
        if sof < 0:
            had_data = len(buf) > 0
            buf.clear()
            return had_data, None
        if sof > 0:
            del buf[:sof]  # drop garbage before SOF
            return True, None

        semantic = bytearray()
        raw = 1
        needed = None
        while needed is None or len(semantic) < needed:
            if raw >= len(buf):
                return False, None  # wait for more data
            b = buf[raw]
            if b == START_OF_FRAME:
                if raw + 1 >= len(buf):
                    return False, None  # wait to see whether this 0x7E is escaped
                if buf[raw + 1] != START_OF_FRAME:
                    del buf[:raw]  # unescaped SOF mid-frame: resync on it
                    return True, None
                semantic.append(START_OF_FRAME)
                raw += 2
            else:
                semantic.append(b)
                raw += 1
            if len(semantic) == 1:
                if semantic[0] > MAX_SERIAL_PAYLOAD:
                    del buf[:1]  # corrupt length
                    return True, None
                needed = 4 + semantic[0]  # Len, Cmd, Pair, Payload, Checksum

        length = semantic[0]
        expected = CHECKSUM_SEED
        for b in semantic[: 3 + length]:
            expected += b * (2 if b == START_OF_FRAME else 1)
        if semantic[3 + length] != (expected & 0xFF):
            del buf[:1]
            return True, None

        message = SerialLinkMessage(semantic[1], semantic[2], bytes(semantic[3 : 3 + length]))
        del buf[:raw]
        return True, message


# Fixed startup frames.
ROOT_HANDSHAKE_REQUEST = encode_serial_frame(0x00, DEVICE_PAIR_TO_DEVICE)      # 7E 00 00 12 9D
ROOT_HANDSHAKE_RESPONSE = encode_serial_frame(0x80, DEVICE_PAIR_FROM_DEVICE)   # 7E 00 80 21 2C


# ---------------------------------------------------------------------------
# Main-board (ControlPanel) commands
#
# Wire IDs come from the command library embedded in MOZA_SDK.dll (commands_as23.json).
# Each command's "requestCmdIds" is [group, id...]: the group goes in the SerialLink
# Command byte and the remaining IDs prefix the payload - verified against Boxflat's
# independently captured wheelbase traffic (MotSetSteer_LimitAngle = [41, 1]). Replies
# come back with group | 0x80 from device pair 0x21, which the screen session ignores.
# ---------------------------------------------------------------------------

CMD_LED_ENABLE = (69, 98)             # index (0-20), enable (0/1) - "set individual status light"
CMD_LED_TEST_ALL = (69, 105)          # enable (0/1)
CMD_SET_WORKING_MODE = (69, 80)       # 0 normal, 1 standby, 2 debug
CMD_ENABLE_LIGHT_SENSOR = (69, 88)    # 0/1 - auto brightness from the ambient-light sensors
CMD_COCKPIT_SIDE = (69, 89)           # 0 left (captain), 1 right (first officer)
CMD_ENABLE_NOTE = (69, 96)            # 0/1 - kneeboard page
CMD_ENABLE_CHECKLIST = (69, 97)       # 0/1 - checklist page
# Lighting groups: 0 = all, 1-3 = individual strips (Cockpit labels them indicators,
# display and backlight - which strip is which is unverified).
CMD_SET_INDICATOR_MODE = (69, 81)     # + group, mode: 0 manual, 1 telemetry, 2 device
CMD_SET_BRIGHTNESS_RANGE = (69, 82)   # + group, 0-15
CMD_SET_BRIGHTNESS = (69, 83)         # + group, 0-255 (brightness used in telemetry/device mode)


# Reads (group 68). The device answers with Command 0xC4 and payload [id, group, value].
# Per the command library, group 1's range/brightness reads carry an extra 0 byte.
CMD_GET_INDICATOR_MODE = (68, 81)     # + group
CMD_GET_BRIGHTNESS_RANGE = (68, 82)   # + group (+ 0 for group 1)
CMD_GET_BRIGHTNESS = (68, 83)         # + group (+ 0 for group 1)
DEVICE_READ_REPLY = 68 | 0x80
DEVICE_WRITE_REPLY = 69 | 0x80
DEVICE_LOG = 0x0E                     # firmware debug log lines, payload [level, ascii text...]

# Brightness on the MB7F, mapped by watching MOZA Cockpit's sliders (all three persist on the device):
#   key backlight  main-board lighting group 1: mode manual, then range 0-15
#   annunciators   main-board lighting group 3: range 0-15
#   screen         screen-board setting 0x01: u32 LE percent 0-100 (Cockpit's 0-15 slider * 100/15).
#                  Main-board group 2 ("screen" in Cockpit's tables) ignores writes on this firmware.
LIGHT_GROUP_KEYS = 1
LIGHT_GROUP_INDICATORS = 3
SETTING_SCREEN_BRIGHTNESS = SETTING_SCREEN_PERCENT


def device_read_ids(cmd: Sequence[int], group: int) -> tuple:
    """Read-command IDs for a lighting group, with group 1's extra 0 where the library has it."""
    ids = tuple(cmd) + (group,)
    if group == 1 and tuple(cmd) in (CMD_GET_BRIGHTNESS_RANGE, CMD_GET_BRIGHTNESS):
        ids += (0,)
    return ids


def encode_device_command(cmd_ids: Sequence[int], args: Sequence[int] = ()) -> bytes:
    """Frame a main-board command: Command = cmd_ids[0], payload = cmd_ids[1:] + args (all bytes)."""
    if not cmd_ids:
        raise ValueError("cmd_ids must contain at least the group byte")
    return encode_serial_frame(cmd_ids[0], DEVICE_PAIR_TO_DEVICE, bytes(list(cmd_ids[1:]) + [a & 0xFF for a in args]))


# ---------------------------------------------------------------------------
# 0x43 tunnel
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TunnelMessage:
    inner_command: int
    is_reply: bool
    inner_payload: bytes


def tunnel_wrap(inner_command: int, inner_payload: bytes = b"", is_reply: bool = False) -> bytes:
    inner_payload = inner_payload or b""
    if len(inner_payload) > MAX_TUNNEL_INNER_PAYLOAD:
        raise ValueError(f"Tunnel inner payload exceeds {MAX_TUNNEL_INNER_PAYLOAD} bytes.")
    command_byte = (inner_command & 0x7F) | (0x80 if is_reply else 0x00)
    return encode_serial_frame(TUNNEL_COMMAND, DEVICE_PAIR_TO_DEVICE, bytes([command_byte]) + inner_payload)


def tunnel_unwrap(message: SerialLinkMessage) -> Optional[TunnelMessage]:
    # The outer command's bit7 is the *outer* reply flag; only the low 7 bits identify the tunnel.
    if not message.payload or (message.command & 0x7F) != TUNNEL_COMMAND:
        return None
    cb = message.payload[0]
    return TunnelMessage(cb & 0x7F, bool(cb & 0x80), message.payload[1:])


DEVICE_INIT_REQUEST = tunnel_wrap(0x00)  # 7E 01 43 12 00 E1


# ---------------------------------------------------------------------------
# Reliable Stream (a tiny TCP over the tunnel)
# ---------------------------------------------------------------------------

class StreamType(enum.IntEnum):
    FIN = 0x00
    TRANS = 0x01
    SYN1 = 0x80
    SYN2 = 0x81


NEGOTIATED_STREAM_VERSION = 2  # "v3": full CRC32 trailer on TRANS


@dataclass(frozen=True)
class StreamRequest:
    destination_port: int
    type: StreamType
    isn: int
    data: bytes = b""
    announced_port: Optional[int] = None
    version: Optional[int] = None


@dataclass(frozen=True)
class StreamAck:
    destination_port: int
    acknowledged_isn: int


def pack_syn(destination_port: int, type_: StreamType, isn: int, announced_port: int, version: int) -> bytes:
    if type_ not in (StreamType.SYN1, StreamType.SYN2):
        raise ValueError("type must be SYN1 or SYN2")
    # DestPort:u16 BE | Magic | ISN:u16 LE | AnnouncedPort:u16 LE | ~Version | Version
    return u16be(destination_port) + bytes([type_]) + u16le(isn) + u16le(announced_port) + bytes([~version & 0xFF, version])


def pack_ack(destination_port: int, acknowledged_isn: int) -> bytes:
    return u16be(destination_port) + u16le(acknowledged_isn)


def pack_trans(destination_port: int, isn: int, data: bytes = b"") -> bytes:
    data = data or b""
    if len(data) > MAX_APPLICATION_CHUNK:
        raise ValueError(f"Application data exceeds the {MAX_APPLICATION_CHUNK}-byte TRANS chunk limit.")
    return u16be(destination_port) + bytes([StreamType.TRANS]) + u16le(isn) + data + u32le(crc32(data))


def pack_fin(destination_port: int, isn: int) -> bytes:
    return u16be(destination_port) + bytes([StreamType.FIN]) + u16le(isn)


def parse_stream_request(payload: bytes) -> StreamRequest:
    if len(payload) < 5:
        raise ProtocolError("Stream request payload is too short.")
    dest = struct.unpack_from(">H", payload, 0)[0]
    kind = payload[2]
    isn = struct.unpack_from("<H", payload, 3)[0]

    if kind in (StreamType.SYN1, StreamType.SYN2):
        if len(payload) != 9:
            raise ProtocolError("Stream SYN payload has the wrong length.")
        announced = struct.unpack_from("<H", payload, 5)[0]
        inverse, version = payload[7], payload[8]
        if inverse != (~version & 0xFF):
            raise ProtocolError("Stream SYN inverse version does not match.")
        return StreamRequest(dest, StreamType(kind), isn, announced_port=announced, version=version)

    if kind == StreamType.TRANS:
        body = payload[5:]
        if len(body) < 4:
            raise ProtocolError("TRANS body is missing its CRC trailer.")
        data, received = body[:-4], struct.unpack_from("<I", body, len(body) - 4)[0]
        if received != crc32(data):
            raise ProtocolError(f"TRANS CRC mismatch: received 0x{received:08X}, expected 0x{crc32(data):08X}.")
        return StreamRequest(dest, StreamType.TRANS, isn, data=bytes(data))

    if kind == StreamType.FIN:
        if len(payload) != 5:
            raise ProtocolError("Stream FIN payload has the wrong length.")
        return StreamRequest(dest, StreamType.FIN, isn)

    raise ProtocolError(f"Unknown Reliable Stream message type 0x{kind:02X}.")


def parse_stream_ack(payload: bytes) -> Optional[StreamAck]:
    if len(payload) < 4:
        return None
    return StreamAck(struct.unpack_from(">H", payload, 0)[0], struct.unpack_from("<H", payload, 2)[0])


# ---------------------------------------------------------------------------
# NetworkPackage: PackageId:u8 | PayloadSize:u32 LE | Payload
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NetworkPackage:
    package_id: int
    payload: bytes


def pack_network_package(package_id: int, payload: bytes = b"") -> bytes:
    return bytes([package_id]) + u32le(len(payload)) + payload


class NetworkPackageExtractor:
    MAX_PAYLOAD = 16 * 1024 * 1024

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> List[NetworkPackage]:
        self._buf.extend(data)
        out = []
        while len(self._buf) >= 5:
            size = struct.unpack_from("<I", self._buf, 1)[0]
            if size > self.MAX_PAYLOAD:
                raise ProtocolError(f"NetworkPackage payload size {size} exceeds the sanity limit.")
            total = 5 + size
            if len(self._buf) < total:
                break
            out.append(NetworkPackage(self._buf[0], bytes(self._buf[5:total])))
            del self._buf[:total]
        return out


# ---------------------------------------------------------------------------
# MCDU channel (port 9050)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class McduClientCapability:
    version: int
    capabilities: int
    udp_listen_port: int
    page_index: int


@dataclass
class TextRow:
    row: int
    segments: List[Tuple[int, str]]  # (start_column, text)


@dataclass
class StyleRow:
    row: int
    segments: List[Tuple[int, bytes]]  # (start_column, style bytes)


def pack_mcdu_init_config(server_capabilities: int = 0, rows: int = MCDU_ROWS, columns: int = MCDU_COLUMNS,
                          keyframe_interval_ms: int = 750) -> bytes:
    # Version | Rows:u16 | Cols:u16 | CellBytes:u16 | ServerCaps | ServerUdpPort:u16 | KeyframeIntervalMs:u16 (all LE)
    payload = (bytes([2]) + u16le(rows) + u16le(columns) + u16le(2) + bytes([server_capabilities])
               + u16le(PORT_MCDU_UDP) + u16le(keyframe_interval_ms))
    return pack_network_package(PKG_MCDU_INIT_CONFIG, payload)


def parse_mcdu_client_capability(payload: bytes) -> Optional[McduClientCapability]:
    if len(payload) != 5:
        return None
    version, caps, port, page = payload[0], payload[1], struct.unpack_from("<H", payload, 2)[0], payload[4]
    if version != 2 or page not in (0, 1):
        return None
    return McduClientCapability(version, caps, port, page)


def _pack_patch_payload(sequence: int, text_rows: Sequence[TextRow], style_rows: Sequence[StyleRow],
                        base_sequence: Optional[int]) -> bytes:
    if len(text_rows) > 255 or len(style_rows) > 255:
        raise ValueError("More than 255 rows.")

    body = bytearray([len(text_rows)])
    for row in text_rows:
        body += bytes([row.row, len(row.segments)])
        for start, text in row.segments:
            utf8 = text.encode("utf-8")
            # ColumnLength counts UTF-16 code units, not UTF-8 bytes.
            columns = len(text.encode("utf-16-le")) // 2
            if columns > 255:
                raise ValueError("Text segment spans more than 255 columns.")
            body += bytes([start, columns]) + u16le(len(utf8)) + utf8
    body.append(len(style_rows))
    for row in style_rows:
        body += bytes([row.row, len(row.segments)])
        for start, styles in row.segments:
            body += bytes([start, len(styles)]) + bytes(styles)

    flags = 0
    if text_rows:
        flags |= 0x01
    if style_rows:
        flags |= 0x02
    if base_sequence is not None:
        flags |= 0x04

    compressed = zlib.compress(bytes(body), 9)
    use_compression = len(compressed) < len(body)
    if use_compression:
        flags |= 0x08

    payload = bytearray([2]) + u32le(sequence) + bytes([flags])
    if base_sequence is not None:
        payload += u32le(base_sequence)
    if use_compression:
        payload += u32be(len(body)) + compressed  # UncompressedSize is big-endian
    else:
        payload += body
    return bytes(payload)


def pack_mcdu_keyframe(sequence: int, text_rows: Sequence[TextRow], style_rows: Sequence[StyleRow]) -> bytes:
    return pack_network_package(PKG_MCDU_KEYFRAME, _pack_patch_payload(sequence, text_rows, style_rows, None))


def pack_mcdu_delta(sequence: int, text_rows: Sequence[TextRow], style_rows: Sequence[StyleRow],
                    base_sequence: Optional[int] = None) -> bytes:
    return pack_network_package(PKG_MCDU_DELTA, _pack_patch_payload(sequence, text_rows, style_rows, base_sequence))


@dataclass
class McduPatch:
    """Decoded keyframe/delta - used by the simulator and tests."""
    keyframe: bool
    sequence: int
    base_sequence: Optional[int]
    text_rows: List[TextRow] = field(default_factory=list)
    style_rows: List[StyleRow] = field(default_factory=list)


def parse_mcdu_patch(package: NetworkPackage) -> McduPatch:
    p = package.payload
    if package.package_id not in (PKG_MCDU_KEYFRAME, PKG_MCDU_DELTA) or len(p) < 6 or p[0] != 2:
        raise ProtocolError("Not an MCDU keyframe/delta.")
    sequence = struct.unpack_from("<I", p, 1)[0]
    flags = p[5]
    off = 6
    base = None
    if flags & 0x04:
        base = struct.unpack_from("<I", p, off)[0]
        off += 4
    if flags & 0x08:
        size = struct.unpack_from(">I", p, off)[0]
        body = zlib.decompress(p[off + 4 :])
        if len(body) != size:
            raise ProtocolError("Decompressed size mismatch.")
    else:
        body = p[off:]

    patch = McduPatch(package.package_id == PKG_MCDU_KEYFRAME, sequence, base)
    i = 0
    count = body[i]; i += 1
    for _ in range(count):
        row, nseg = body[i], body[i + 1]; i += 2
        segs = []
        for _ in range(nseg):
            start, _cols = body[i], body[i + 1]
            n = struct.unpack_from("<H", body, i + 2)[0]
            segs.append((start, body[i + 4 : i + 4 + n].decode("utf-8")))
            i += 4 + n
        patch.text_rows.append(TextRow(row, segs))
    count = body[i]; i += 1
    for _ in range(count):
        row, nseg = body[i], body[i + 1]; i += 2
        segs = []
        for _ in range(nseg):
            start, n = body[i], body[i + 1]
            segs.append((start, bytes(body[i + 2 : i + 2 + n])))
            i += 2 + n
        patch.style_rows.append(StyleRow(row, segs))
    return patch


# ---------------------------------------------------------------------------
# Settings channel (port 9020)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SettingFrame:
    setting_id: int
    data: bytes


def pack_setting_frame(setting_id: int, data: bytes = b"") -> bytes:
    """FF | Size:u32 LE | CRC32:u32 LE | SettingId:i32 LE | Data  (Size = 4 + len(Data); CRC covers id+data)."""
    body = u32le(setting_id) + (data or b"")
    return b"\xFF" + u32le(len(body)) + u32le(crc32(body)) + body


def pack_time_sync(unix_seconds: int, utc_offset_seconds: int) -> bytes:
    return pack_setting_frame(SETTING_TIME_SYNC, u64le(unix_seconds) + i32le(utc_offset_seconds))


def pack_protocol_version(version: int = 3, reserved: int = 0) -> bytes:
    return pack_setting_frame(SETTING_PROTOCOL_VERSION, u32le(version) + i32le(reserved))


SETTINGS_PREAMBLE_HEADER = bytes([0x07, 0x01, 0x00, 0x00, 0x00])


def parse_settings_handshake(buf: bytes) -> Optional[Tuple[bytes, bytes, int]]:
    """Parse `07 01 00 00 00 | 00 | McuIdLen | SettingCount | McuId | SupportedIds`.

    Returns (mcu_id, supported_setting_ids, consumed) or None if more bytes are needed.
    """
    if len(buf) < 8:
        return None
    if bytes(buf[:5]) != SETTINGS_PREAMBLE_HEADER:
        raise ProtocolError("Unexpected settings preamble header.")
    if buf[5] != 0x00:
        raise ProtocolError(f"Unexpected settings handshake marker 0x{buf[5]:02X}.")
    id_len, count = buf[6], buf[7]
    total = 8 + id_len + count
    if len(buf) < total:
        return None
    return bytes(buf[8 : 8 + id_len]), bytes(buf[8 + id_len : total]), total


class SettingsFrameExtractor:
    """Pulls settings values out of the 9020 stream, which mixes three formats:

    * a one-time unframed legacy block right after the handshake preamble,
    * ongoing legacy echoes (1 id byte + fixed-size value) of those same settings,
    * the general `FF | Size | CRC32 | Id | Data` frame.
    """

    LEGACY_FIELDS = ((0x01, 4), (0x05, 1), (0x04, 8), (0x06, 1))
    LEGACY_SIZES = dict(LEGACY_FIELDS)
    MAX_PAYLOAD = 16 * 1024 * 1024

    def __init__(self) -> None:
        self._buf = bytearray()
        self._legacy_block_pending = False

    def expect_legacy_current_values(self) -> None:
        self._legacy_block_pending = True

    def feed(self, data: bytes) -> List[SettingFrame]:
        self._buf.extend(data)
        frames: List[SettingFrame] = []
        if self._legacy_block_pending:
            if not self._extract_legacy_block(frames):
                return frames
            self._legacy_block_pending = False
        self._extract_legacy_echoes(frames)
        self._extract_framed(frames)
        return frames

    def _extract_legacy_block(self, frames: List[SettingFrame]) -> bool:
        buf = self._buf
        if not buf or buf[0] != self.LEGACY_FIELDS[0][0]:
            return True  # block isn't coming; stop waiting
        total = sum(1 + size for _, size in self.LEGACY_FIELDS)
        if len(buf) < total:
            return False
        off, extracted = 0, []
        for sid, size in self.LEGACY_FIELDS:
            if buf[off] != sid:
                return True  # malformed: abandon rather than misdeliver
            extracted.append(SettingFrame(sid, bytes(buf[off + 1 : off + 1 + size])))
            off += 1 + size
        del buf[:total]
        frames.extend(extracted)
        return True

    def _extract_legacy_echoes(self, frames: List[SettingFrame]) -> None:
        buf = self._buf
        while buf:
            size = self.LEGACY_SIZES.get(buf[0])
            if size is None:
                return
            if len(buf) < 1 + size:
                return
            frames.append(SettingFrame(buf[0], bytes(buf[1 : 1 + size])))
            del buf[: 1 + size]

    def _extract_framed(self, frames: List[SettingFrame]) -> None:
        buf = self._buf
        while buf:
            if buf[0] != 0xFF:
                start = buf.find(0xFF)
                if start < 0:
                    buf.clear()
                    return
                del buf[:start]
            if len(buf) < 9:
                return
            size, received = struct.unpack_from("<II", buf, 1)
            if size < 4 or size > self.MAX_PAYLOAD:
                del buf[:1]
                continue
            total = 9 + size
            if len(buf) < total:
                return
            body = bytes(buf[9:total])
            del buf[:total]
            if received != crc32(body):
                raise ProtocolError(f"Settings frame CRC mismatch: received 0x{received:08X}, expected 0x{crc32(body):08X}.")
            frames.append(SettingFrame(struct.unpack_from("<I", body, 0)[0], body[4:]))


# ---------------------------------------------------------------------------
# Qt container formats (big-endian length prefixes), used inside some settings values
# ---------------------------------------------------------------------------

def pack_qstring(value: str) -> bytes:
    encoded = (value or "").encode("utf-16-be")
    return u32be(len(encoded)) + encoded


def pack_qbytearray(data: bytes) -> bytes:
    return u32be(len(data or b"")) + (data or b"")
