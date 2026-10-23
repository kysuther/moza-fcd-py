"""Session layer: Reliable Stream connections, channels, and the screen session state machine.

Still pure - no threads, no serial port. Every timing decision takes an explicit
`now` (seconds, from time.monotonic()), and all output goes through a `send(bytes)`
callable. `device.FcdScreen` drives this over a real port; the tests drive it
against a simulated device.
"""

from __future__ import annotations

import collections
import datetime
import enum
import time
from typing import Callable, Deque, Dict, Iterable, List, Optional

from . import protocol as p
from .page import COLUMNS, ROWS, Page

SendRaw = Callable[[bytes], None]
StreamSend = Callable[[bytes, bool], None]  # (reliable-stream payload, is_reply)


# ---------------------------------------------------------------------------
# Reliable Stream
# ---------------------------------------------------------------------------

class StreamConnection:
    """One device-initiated connection on a service port. Stop-and-wait: one TRANS/FIN in flight."""

    RETRANSMIT_S = 0.6
    HEARTBEAT_S = 3.0
    MAX_RETRIES = 8
    TIMEOUT_S = 10.0

    def __init__(self, service_port: int, local_port: int, syn1: p.StreamRequest, now: float) -> None:
        self.service_port = service_port
        self.local_port = local_port
        self.peer_port = syn1.announced_port
        self.version = min(p.NEGOTIATED_STREAM_VERSION, syn1.version)
        self.peer_last_isn = syn1.isn
        self.send_isn = (local_port + 1) & 0xFFFF
        self.established = False
        self.peer_closed = False
        self.local_fin_acked = False
        self._pending_isn: Optional[int] = None
        self._pending_is_fin = False
        self._pending_wire = b""
        self._pending_sent_at = 0.0
        self._pending_retries = 0
        self._last_activity = now
        self._rx = bytearray()

    @property
    def fully_closed(self) -> bool:
        return self.peer_closed and self.local_fin_acked

    @property
    def has_pending(self) -> bool:
        return self._pending_isn is not None

    @property
    def retransmit_exhausted(self) -> bool:
        return self.has_pending and self._pending_retries >= self.MAX_RETRIES

    def begin_syn2(self, now: float) -> bytes:
        # SYN2 uses ISN = local port, which is also what the establishing ACK acknowledges.
        wire = p.pack_syn(self.peer_port, p.StreamType.SYN2, self.local_port, self.local_port, self.version)
        self._arm(self.local_port, False, wire, now)
        return wire

    def accept_ack(self, ack: p.StreamAck, now: float) -> bool:
        if ack.destination_port != self.local_port or self._pending_isn is None:
            return False
        if ack.acknowledged_isn != self._pending_isn:
            return False
        if not self.established:
            if ack.acknowledged_isn != self.local_port:
                return False
            self.established = True
            self._clear()
            self._last_activity = now
            return True
        was_fin = self._pending_is_fin
        self._clear()
        self._last_activity = now
        if was_fin:
            self.local_fin_acked = True
            self.established = False
        return True

    def accept_request(self, req: p.StreamRequest, now: float) -> int:
        """Returns the cumulative ACK value; duplicates are re-ACKed but not delivered."""
        if req.isn == ((self.peer_last_isn + 1) & 0xFFFF):
            self.peer_last_isn = req.isn
            if req.type == p.StreamType.TRANS and req.data:
                self._rx.extend(req.data)
            elif req.type == p.StreamType.FIN:
                self.peer_closed = True
            self._last_activity = now
        return self.peer_last_isn

    def read(self) -> bytes:
        data = bytes(self._rx)
        self._rx.clear()
        return data

    def try_trans(self, data: bytes, now: float) -> Optional[bytes]:
        if not self.established or self.peer_closed or self.has_pending:
            return None
        isn = self._next_isn()
        wire = p.pack_trans(self.peer_port, isn, data)
        self._arm(isn, False, wire, now)
        return wire

    def try_fin(self, now: float) -> Optional[bytes]:
        if not self.established or self.local_fin_acked or self.has_pending:
            return None
        isn = self._next_isn()
        wire = p.pack_fin(self.peer_port, isn)
        self._arm(isn, True, wire, now)
        return wire

    def try_retransmit(self, now: float) -> Optional[bytes]:
        if not self.has_pending or now - self._pending_sent_at < self.RETRANSMIT_S:
            return None
        if self._pending_retries >= self.MAX_RETRIES:
            return None
        self._pending_retries += 1
        self._pending_sent_at = now
        return self._pending_wire

    def needs_heartbeat(self, now: float) -> bool:
        return (self.established and not self.has_pending and not self.peer_closed
                and now - self._last_activity >= self.HEARTBEAT_S)

    def _next_isn(self) -> int:
        isn = self.send_isn
        self.send_isn = (self.send_isn + 1) & 0xFFFF
        return isn

    def _arm(self, isn: int, is_fin: bool, wire: bytes, now: float) -> None:
        self._pending_isn, self._pending_is_fin, self._pending_wire = isn, is_fin, wire
        self._pending_sent_at, self._pending_retries = now, 0

    def _clear(self) -> None:
        self._pending_isn, self._pending_wire, self._pending_retries = None, b"", 0


class StreamMultiplexer:
    """Owns one connection per accepted service port. Only the device initiates (SYN1)."""

    def __init__(self, send: StreamSend, accepted_ports: Iterable[int],
                 on_error: Callable[[str], None] = lambda m: None,
                 on_established: Callable[[int], None] = lambda port: None) -> None:
        self._send = send
        self._accepted = set(accepted_ports)
        self._by_local: Dict[int, StreamConnection] = {}
        self._by_service: Dict[int, StreamConnection] = {}
        self._queues: Dict[int, Deque[bytes]] = {}
        self._closing = False
        self.on_error = on_error
        self.on_established = on_established

    def connection(self, service_port: int) -> Optional[StreamConnection]:
        return self._by_service.get(service_port)

    def queued_chunks(self, service_port: int) -> int:
        return len(self._queues.get(service_port, ()))

    @property
    def close_complete(self) -> bool:
        return not self._by_local

    def handle(self, payload: bytes, is_reply: bool, now: float) -> None:
        if is_reply:
            ack = p.parse_stream_ack(payload)
            if ack:
                self._handle_ack(ack, now)
            return
        try:
            req = p.parse_stream_request(payload)
        except p.ProtocolError as e:
            self.on_error(str(e))
            return
        if req.type == p.StreamType.SYN1:
            self._handle_syn1(req, now)
        else:
            self._handle_request(req, now)

    def send(self, service_port: int, data: bytes) -> bool:
        """Chunk and queue; chunks go out one at a time from tick() as ACKs arrive."""
        if service_port not in self._by_service:
            return False
        q = self._queues.setdefault(service_port, collections.deque())
        for off in range(0, len(data), p.MAX_APPLICATION_CHUNK):
            q.append(data[off : off + p.MAX_APPLICATION_CHUNK])
        return True

    def tick(self, now: float) -> None:
        for conn in list(self._by_local.values()):
            wire = conn.try_retransmit(now)
            if wire is not None:
                self._send(wire, False)
                continue
            if conn.retransmit_exhausted:
                self.on_error(f"Reliable Stream retransmit exhausted on service port {conn.service_port}.")
                self._remove(conn)
                continue
            if not conn.established or conn.has_pending:
                continue
            q = self._queues.get(conn.service_port)
            if q:
                wire = conn.try_trans(q.popleft(), now)
                if wire is not None:
                    self._send(wire, False)
                continue
            if conn.needs_heartbeat(now):
                wire = conn.try_trans(b"", now)
                if wire is not None:
                    self._send(wire, False)

    def begin_close(self, now: float) -> None:
        self._closing = True
        for conn in list(self._by_local.values()):
            if conn.established and not conn.local_fin_acked and not conn.has_pending:
                wire = conn.try_fin(now)
                if wire is not None:
                    self._send(wire, False)

    def _handle_ack(self, ack: p.StreamAck, now: float) -> None:
        conn = self._by_local.get(ack.destination_port)
        if conn is None:
            return
        was_established = conn.established
        if not conn.accept_ack(ack, now):
            return
        if not was_established and conn.established:
            self.on_established(conn.service_port)
        elif conn.fully_closed:
            self._remove(conn)
        elif conn.peer_closed and not conn.local_fin_acked and not conn.has_pending:
            wire = conn.try_fin(now)
            if wire is not None:
                self._send(wire, False)

    def _handle_syn1(self, syn1: p.StreamRequest, now: float) -> None:
        if self._closing or syn1.destination_port not in self._accepted:
            return
        current = self._by_service.get(syn1.destination_port)
        if current is not None:
            if not current.established and current.peer_port == syn1.announced_port:
                self._send(current.begin_syn2(now), False)  # duplicate SYN1: resend SYN2 now
                return
            self._remove(current)  # device is starting over
        local = self._allocate_port(syn1.announced_port or 0)
        conn = StreamConnection(syn1.destination_port, local, syn1, now)
        self._by_local[local] = conn
        self._by_service[syn1.destination_port] = conn
        self._send(conn.begin_syn2(now), False)

    def _handle_request(self, req: p.StreamRequest, now: float) -> None:
        conn = self._by_local.get(req.destination_port)
        if conn is None:
            return
        peer_was_closed = conn.peer_closed
        acked = conn.accept_request(req, now)
        self._send(p.pack_ack(conn.peer_port, acked), True)
        if req.type == p.StreamType.FIN and not peer_was_closed and conn.peer_closed:
            if conn.fully_closed:
                self._remove(conn)
            elif not conn.has_pending:
                wire = conn.try_fin(now)
                if wire is not None:
                    self._send(wire, False)

    def _allocate_port(self, preferred: int) -> int:
        port = preferred
        while port in p.RESERVED_PORTS or port in self._by_local:
            port = (port + 1) & 0xFFFF
            if port == preferred:
                raise RuntimeError("No Reliable Stream local port is available.")
        return port

    def _remove(self, conn: StreamConnection) -> None:
        self._by_local.pop(conn.local_port, None)
        self._by_service.pop(conn.service_port, None)
        self._queues.pop(conn.service_port, None)


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

def _local_utc_offset_seconds() -> int:
    offset = datetime.datetime.now().astimezone().utcoffset()
    return int(offset.total_seconds()) if offset else 0


class SettingsChannel:
    """Port 9020: wait for the handshake preamble, send time sync + protocol version,
    then collect reported settings for a short window before declaring ready."""

    COLLECTION_WINDOW_S = 4.0

    def __init__(self, mux: StreamMultiplexer) -> None:
        self._mux = mux
        self._preamble = bytearray()
        self._extractor = p.SettingsFrameExtractor()
        self._past_preamble = False
        self._deadline: Optional[float] = None
        self.settings: Dict[int, bytes] = {}
        self.mcu_id: Optional[bytes] = None
        self.supported_ids: Optional[bytes] = None
        self.on_ready: Callable[[], None] = lambda: None
        self.on_setting: Callable[[int, bytes], None] = lambda sid, data: None

    def _first_byte(self, sid: int) -> Optional[int]:
        data = self.settings.get(sid)
        return data[0] if data else None

    @property
    def cabin_position(self) -> Optional[int]:
        return self._first_byte(p.SETTING_CABIN_POSITION)

    @property
    def display_mode(self) -> Optional[int]:
        return self._first_byte(p.SETTING_DISPLAY_MODE)

    def on_data(self, data: bytes, now: float) -> None:
        if not self._past_preamble:
            self._preamble.extend(data)
            parsed = p.parse_settings_handshake(self._preamble)
            if parsed is None:
                return
            self.mcu_id, self.supported_ids, consumed = parsed
            remainder = bytes(self._preamble[consumed:])
            self._preamble.clear()
            self._past_preamble = True
            self._extractor.expect_legacy_current_values()
            self._deadline = now + self.COLLECTION_WINDOW_S
            self._mux.send(p.PORT_SETTINGS, p.pack_time_sync(int(time.time()), _local_utc_offset_seconds()))
            self._mux.send(p.PORT_SETTINGS, p.pack_protocol_version(3, 0))
            if remainder:
                self._process(remainder)
            return
        self._process(data)
        self.tick(now)

    def tick(self, now: float) -> None:
        if self._deadline is not None and now >= self._deadline:
            self._deadline = None
            self.on_ready()

    def request(self, setting_id: int, data: bytes) -> bool:
        return self._mux.send(p.PORT_SETTINGS, p.pack_setting_frame(setting_id, data))

    def _process(self, data: bytes) -> None:
        for frame in self._extractor.feed(data):
            self.settings[frame.setting_id] = frame.data
            self.on_setting(frame.setting_id, frame.data)


class TelemetryChannel:
    """Port 9010: echo the device's 4-byte registration token so it keeps the channel alive.
    (The device can also subscribe to telemetry here; that part isn't implemented.)"""

    def __init__(self, mux: StreamMultiplexer) -> None:
        self._mux = mux
        self._extractor = p.NetworkPackageExtractor()
        self._skipped_leading_byte = False
        self.on_package: Callable[[p.NetworkPackage], None] = lambda pkg: None

    def on_data(self, data: bytes) -> None:
        if not self._skipped_leading_byte:
            if not data:
                return
            self._skipped_leading_byte = True  # device sends one raw 0xFF first
            data = data[1:]
        for pkg in self._extractor.feed(data):
            self.on_package(pkg)
            if pkg.package_id == p.PKG_TELEMETRY_TOKEN and len(pkg.payload) == 4:
                self._mux.send(p.PORT_TELEMETRY, p.pack_network_package(p.PKG_TELEMETRY_TOKEN, pkg.payload))


class FrameBuilder:
    """Page -> next MCDU frame: keyframe first, then deltas of changed rows only."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._next_sequence = 1
        self._last: Optional[Page] = None

    def build_next(self, page: Page) -> Optional[bytes]:
        if self._last is None:
            return self.force_keyframe(page)
        changed = [r for r in range(ROWS) if not self._last.row_equals(page, r)]
        self._last = page.copy()
        if not changed:
            return None
        seq = self._take_sequence()
        return p.pack_mcdu_delta(seq, [self._text(page, r) for r in changed], [self._style(page, r) for r in changed])

    def force_keyframe(self, page: Page) -> bytes:
        # Sequence must strictly increase, so this never resets it.
        self._last = page.copy()
        seq = self._take_sequence()
        return p.pack_mcdu_keyframe(seq, [self._text(page, r) for r in range(ROWS)],
                                    [self._style(page, r) for r in range(ROWS)])

    def _take_sequence(self) -> int:
        seq = self._next_sequence
        self._next_sequence = (self._next_sequence + 1) & 0xFFFFFFFF
        return seq

    @staticmethod
    def _text(page: Page, row: int) -> p.TextRow:
        return p.TextRow(row, [(0, page.row_text(row))])

    @staticmethod
    def _style(page: Page, row: int) -> p.StyleRow:
        return p.StyleRow(row, [(0, page.row_styles(row))])


class McduChannel:
    """Port 9050: send InitConfig, wait for ClientCapability, then stream pages."""

    def __init__(self, mux: StreamMultiplexer, keyframe_interval_ms: int = 750) -> None:
        self._mux = mux
        self._extractor = p.NetworkPackageExtractor()
        self._builder = FrameBuilder()
        self._pending: Optional[Page] = None
        self.keyframe_interval_ms = keyframe_interval_ms
        self.capability: Optional[p.McduClientCapability] = None
        self.on_capability: Callable[[p.McduClientCapability], None] = lambda cap: None

    def start(self) -> None:
        self.capability = None
        self._builder.reset()
        self._mux.send(p.PORT_MCDU_TCP, p.pack_mcdu_init_config(keyframe_interval_ms=self.keyframe_interval_ms))

    def on_data(self, data: bytes) -> None:
        for pkg in self._extractor.feed(data):
            if pkg.package_id != p.PKG_MCDU_CLIENT_CAPABILITY:
                continue
            cap = p.parse_mcdu_client_capability(pkg.payload)
            if cap is None:
                continue
            self.capability = cap
            self.on_capability(cap)
            self._send_pending()

    def submit(self, page: Page) -> None:
        self._pending = page.copy()
        self._send_pending()

    def force_resend(self) -> None:
        if self.capability is not None and self._pending is not None:
            self._mux.send(p.PORT_MCDU_TCP, self._builder.force_keyframe(self._pending))

    def _send_pending(self) -> None:
        if self.capability is None or self._pending is None:
            return
        frame = self._builder.build_next(self._pending)
        if frame is not None:
            self._mux.send(p.PORT_MCDU_TCP, frame)


# ---------------------------------------------------------------------------
# Screen session
# ---------------------------------------------------------------------------

class SessionState(enum.Enum):
    CLOSED = "closed"
    ROOT_HANDSHAKE = "root_handshake"
    DEVICE_INIT = "device_init"
    RUNNING = "running"
    FAULTED = "faulted"


class ScreenSession:
    """Root handshake -> device init -> settings + MCDU channels -> display mode 1 (MCDU)."""

    SHUTDOWN_GRACE_S = 3.0

    def __init__(self, send_raw: SendRaw, keyframe_interval_ms: int = 750) -> None:
        self._send_raw = send_raw
        self._decoder = p.SerialLinkDecoder()
        self.mux = StreamMultiplexer(
            lambda payload, is_reply: send_raw(p.tunnel_wrap(p.STREAM_INNER_COMMAND, payload, is_reply)),
            (p.PORT_TELEMETRY, p.PORT_SETTINGS, p.PORT_MCDU_TCP),
            on_error=lambda m: self.on_error(m),
        )
        self.settings = SettingsChannel(self.mux)
        self.settings.on_ready = self._on_settings_ready
        self.settings.on_setting = lambda sid, data: self.on_setting(sid, data)
        self.telemetry = TelemetryChannel(self.mux)
        self.mcdu = McduChannel(self.mux, keyframe_interval_ms)
        self.mcdu.on_capability = lambda cap: (self.on_trace("MCDU ClientCapability received."), self._try_enter_mcdu_mode())

        self.state = SessionState.CLOSED
        self.mcdu_mode_active = False
        self._mcdu_started = False
        self._settings_ready = False
        self._cabin_fired = False
        self._shutting_down = False
        self._close_begun = False
        self._shutdown_deadline: Optional[float] = None
        self._now = 0.0

        # Event hooks - assign your own callables.
        self.on_trace: Callable[[str], None] = lambda m: None
        self.on_error: Callable[[str], None] = lambda m: None
        self.on_setting: Callable[[int, bytes], None] = lambda sid, data: None
        self.on_cabin_position: Callable[[int], None] = lambda pos: None
        self.on_ready: Callable[[], None] = lambda: None  # fired once MCDU mode is entered
        self.on_device_reply: Callable[[int, bytes], None] = lambda cmd, payload: None  # main-board replies
        self.on_device_log: Callable[[str], None] = lambda text: None                   # firmware log lines

    # --- driving -------------------------------------------------------------------

    def start(self) -> None:
        self.state = SessionState.ROOT_HANDSHAKE
        self._send_raw(p.ROOT_HANDSHAKE_REQUEST)

    def feed(self, data: bytes, now: float) -> None:
        self._now = now
        for msg in self._decoder.feed(data):
            self._dispatch(msg, now)

    def tick(self, now: float) -> None:
        self._now = now
        if self.state != SessionState.RUNNING:
            return
        self.mux.tick(now)
        self.settings.tick(now)
        if self._shutting_down and not self._close_begun:
            self._close_begun = True
            self.mux.begin_close(now)

    # --- application API -----------------------------------------------------------

    def submit_page(self, page: Page) -> None:
        self.mcdu.submit(page)

    def force_resend(self) -> None:
        self.mcdu.force_resend()

    def set_display_mode(self, mode: int) -> bool:
        """0 = the device's own UI, 1 = MCDU (host-driven grid)."""
        return self.settings.request(p.SETTING_DISPLAY_MODE, bytes([mode & 0xFF]))

    def request_setting(self, setting_id: int, data: bytes) -> bool:
        return self.settings.request(setting_id, data)

    def send_device_command(self, cmd_ids, args=()) -> bool:
        """Send a main-board command (lights, brightness, ...) - see protocol.CMD_*.
        Only once the session is running, so it can't be mistaken for handshake traffic."""
        if self.state != SessionState.RUNNING:
            return False
        self._send_raw(p.encode_device_command(cmd_ids, args))
        return True

    def begin_shutdown(self, now: float) -> None:
        self._shutdown_deadline = now + self.SHUTDOWN_GRACE_S
        self._shutting_down = True

    @property
    def shutdown_complete(self) -> bool:
        if self.state != SessionState.RUNNING:
            return self._shutting_down
        if not self._close_begun:
            return False
        return self.mux.close_complete or (self._shutdown_deadline is not None and self._now >= self._shutdown_deadline)

    # --- internals -----------------------------------------------------------------

    def _dispatch(self, msg: p.SerialLinkMessage, now: float) -> None:
        if self.state == SessionState.ROOT_HANDSHAKE:
            if msg.command == 0x80 and msg.device_pair == p.DEVICE_PAIR_FROM_DEVICE:
                self.state = SessionState.DEVICE_INIT
                self.on_trace("Root handshake OK, sending device init.")
                self._send_raw(p.DEVICE_INIT_REQUEST)
            else:
                self._fault(f"Unexpected reply to root handshake (Command=0x{msg.command:02X} Pair=0x{msg.device_pair:02X}).")
        elif self.state == SessionState.DEVICE_INIT:
            # Any upstream frame confirms init - often the device's first SYN1.
            if msg.device_pair != p.DEVICE_PAIR_FROM_DEVICE:
                return
            self.state = SessionState.RUNNING
            self.on_trace("Device init confirmed.")
            self._dispatch_running(msg, now)
        elif self.state == SessionState.RUNNING:
            self._dispatch_running(msg, now)

    def _dispatch_running(self, msg: p.SerialLinkMessage, now: float) -> None:
        if msg.device_pair == p.DEVICE_PAIR_FROM_DEVICE:
            if msg.command in (p.DEVICE_READ_REPLY, p.DEVICE_WRITE_REPLY):
                self.on_device_reply(msg.command, bytes(msg.payload))
                return
            if msg.command == p.DEVICE_LOG:
                self.on_device_log(bytes(msg.payload[1:]).decode("ascii", "replace").strip())
                return
        tunnel = p.tunnel_unwrap(msg)
        if tunnel is None or tunnel.inner_command != p.STREAM_INNER_COMMAND:
            return
        self.mux.handle(tunnel.inner_payload, tunnel.is_reply, now)
        if tunnel.is_reply:
            return

        conn = self.mux.connection(p.PORT_MCDU_TCP)
        if not self._mcdu_started and conn is not None and conn.established:
            self._mcdu_started = True
            self.mcdu.start()
            self.on_trace("MCDU (9050) connection established, InitConfig sent.")

        self._drain(p.PORT_SETTINGS, lambda b: (self.settings.on_data(b, now), self._try_cabin_position()))
        self._drain(p.PORT_TELEMETRY, self.telemetry.on_data)
        if self._mcdu_started:
            self._drain(p.PORT_MCDU_TCP, self.mcdu.on_data)

    def _drain(self, port: int, handler: Callable[[bytes], object]) -> None:
        conn = self.mux.connection(port)
        if conn is None:
            return
        data = conn.read()
        if data:
            handler(data)

    def _try_cabin_position(self) -> None:
        pos = self.settings.cabin_position
        if not self._cabin_fired and pos is not None:
            self._cabin_fired = True
            self.on_cabin_position(pos)

    def _on_settings_ready(self) -> None:
        self._settings_ready = True
        self.on_trace(f"Settings ready: displayMode={self.settings.display_mode} cabinPosition={self.settings.cabin_position}.")
        self._try_enter_mcdu_mode()

    def _try_enter_mcdu_mode(self) -> None:
        if self.mcdu_mode_active or not self._settings_ready or self.mcdu.capability is None:
            return
        self.mcdu_mode_active = True
        # Deliberately 0 then 1: a same-value write gets no repaint, and an unclean
        # previous session may have left it at 1 already.
        self.on_trace("Forcing displayMode 0 then 1 (MCDU).")
        self.set_display_mode(0)
        self.set_display_mode(1)
        self.on_ready()

    def _fault(self, message: str) -> None:
        self.state = SessionState.FAULTED
        self.on_error(message)
