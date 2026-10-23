"""Discord voice controls and notifications, through the Discord desktop app's local RPC (a named pipe).

Uses your own Discord application (config [discord] client_id / client_secret / redirect_uri). The first
time, Discord asks you to approve it; the tokens are then kept in discord_token.json and refreshed
automatically. Needs the desktop app running and signed in.

What it provides: your mute/deafen state (and toggles), the voice channel you're in and who's there,
and desktop notifications as they arrive (NOTIFICATION_CREATE). Discord doesn't expose unread counts,
so the MSG light means "a notification arrived" until you look at the list or it times out.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time
import uuid
from typing import Callable, Dict, Optional

from . import Worker
from .. import net
from ..config import APP_DIR

TOKEN_PATH = os.path.join(APP_DIR, "discord_token.json")
TOKEN_URL = "https://discord.com/api/oauth2/token"
SCOPES = ["rpc", "rpc.voice.read", "rpc.voice.write", "rpc.notifications.read", "identify"]
OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 3, 4
RETRY_S = 10.0
CHANNEL_EVERY_S = 15.0
MAX_NOTIFICATIONS = 10


class PipeTransport:
    """The desktop app's named pipe. available() peeks, so reads never block the service thread."""

    def __init__(self) -> None:
        self.f = None

    def connect(self) -> bool:
        for i in range(10):
            try:
                self.f = open(rf"\\.\pipe\discord-ipc-{i}", "r+b", buffering=0)
                return True
            except OSError:
                continue
        return False

    def available(self) -> int:
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(self.f.fileno())
        avail = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.PeekNamedPipe(ctypes.c_void_p(handle), None, 0, None, ctypes.byref(avail), None)
        if not ok:
            raise OSError("Discord closed the connection")
        return avail.value

    def read(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.f.read(n - len(buf))
            if not chunk:
                raise OSError("Discord closed the connection")
            buf += chunk
        return buf

    def write(self, data: bytes) -> None:
        self.f.write(data)

    def close(self) -> None:
        try:
            self.f.close()
        except Exception:
            pass


def encode_frame(op: int, payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return struct.pack("<II", op, len(body)) + body


def member_name(vs: dict) -> str:
    user = vs.get("user") or {}
    return vs.get("nick") or user.get("global_name") or user.get("username") or "?"


def parse_channel(data: Optional[dict]) -> Optional[dict]:
    if not data:
        return None
    members = []
    for vs in data.get("voice_states") or []:
        state = vs.get("voice_state") or {}
        members.append({"name": member_name(vs),
                        "mute": bool(state.get("self_mute") or state.get("mute")),
                        "deaf": bool(state.get("self_deaf") or state.get("deaf"))})
    return {"name": data.get("name") or "", "members": members}


class DiscordService(Worker):
    name = "discord"
    interval = 0.2

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str = "http://localhost",
                 light_minutes: float = 10, transport_factory=PipeTransport, token_path: str = TOKEN_PATH,
                 post=net.post_form, clock=time.monotonic, wall_clock=time.time) -> None:
        super().__init__()
        self.client_id, self.client_secret = str(client_id or "").strip(), str(client_secret or "").strip()
        self.redirect_uri = redirect_uri
        self.light_s = float(light_minutes) * 60
        self.transport_factory = transport_factory
        self.token_path = token_path
        self.post = post
        self.clock, self.wall_clock = clock, wall_clock
        self.t = None
        self._handlers: Dict[str, Callable] = {}
        self._retry_at = 0.0
        self._channel_at = 0.0
        self._authed = False
        configured = bool(self.client_id and self.client_secret)
        self.snapshot = {"configured": configured, "state": "CONNECTING" if configured else "NOT SET UP",
                         "user": "", "mute": None, "deaf": None, "channel": None, "notifications": [],
                         "unseen": 0, "error": ""}

    def setup(self) -> None:
        if sys.platform != "win32" and self.transport_factory is PipeTransport:
            raise RuntimeError("Windows only")

    def _update(self, **kw) -> None:
        self.snapshot = {**self.snapshot, **kw}

    # --- main loop ---------------------------------------------------------------------

    def poll(self) -> None:
        if not self.snapshot["configured"]:
            return
        if self.t is None:
            if self.clock() >= self._retry_at:
                self._connect()
            return
        try:
            while self.t is not None and self.t.available() >= 8:
                op, length = struct.unpack("<II", self.t.read(8))
                self._on_frame(op, json.loads(self.t.read(length).decode("utf-8")) if length else {})
            if self._authed and self.clock() >= self._channel_at:
                self._channel_at = self.clock() + CHANNEL_EVERY_S
                self._send("GET_SELECTED_VOICE_CHANNEL", {}, self._on_channel)
        except (OSError, ValueError) as e:
            self._drop("DISCORD NOT RUNNING", str(e))
        self._expire_unseen()

    def teardown(self) -> None:
        if self.t:
            self.t.close()

    def _connect(self) -> None:
        t = self.transport_factory()
        if not t.connect():
            self._update(state="DISCORD NOT RUNNING")
            self._retry_at = self.clock() + RETRY_S
            return
        self.t = t
        self._update(state="CONNECTING", error="")
        self.t.write(encode_frame(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id}))

    def _drop(self, state: str, error: str = "") -> None:
        if self.t:
            self.t.close()
        self.t = None
        self._authed = False
        self._handlers.clear()
        self._retry_at = self.clock() + RETRY_S
        self._update(state=state, error=error, channel=None, mute=None, deaf=None)

    def _send(self, cmd: str, args: dict, handler: Optional[Callable] = None, evt: Optional[str] = None) -> None:
        nonce = str(uuid.uuid4())
        if handler:
            self._handlers[nonce] = handler
        msg = {"cmd": cmd, "args": args, "nonce": nonce}
        if evt:
            msg["evt"] = evt
        self.t.write(encode_frame(OP_FRAME, msg))

    def _on_frame(self, op: int, msg: dict) -> None:
        if op == OP_PING:
            self.t.write(encode_frame(OP_PONG, msg))
            return
        if op == OP_CLOSE:
            self._drop("ERROR", str(msg.get("message") or msg.get("code") or "closed").upper())
            return
        if msg.get("cmd") == "DISPATCH":
            self._on_event(msg.get("evt"), msg.get("data") or {})
            return
        handler = self._handlers.pop(msg.get("nonce"), None)
        if handler:
            handler(msg.get("data") or {}, msg.get("evt") == "ERROR")

    def _on_event(self, evt: str, data: dict) -> None:
        if evt == "READY":
            self._authenticate()
        elif evt == "VOICE_SETTINGS_UPDATE":
            self._update(mute=data.get("mute"), deaf=data.get("deaf"))
        elif evt == "VOICE_CHANNEL_SELECT":
            self._channel_at = 0.0                      # look the new channel up on the next poll
        elif evt == "NOTIFICATION_CREATE":
            note = {"title": data.get("title") or "", "body": data.get("body") or "", "at": self.wall_clock()}
            notes = [note] + self.snapshot["notifications"]
            self._update(notifications=notes[:MAX_NOTIFICATIONS], unseen=self.snapshot["unseen"] + 1)

    # --- authorisation ---------------------------------------------------------------------

    def _load_token(self) -> dict:
        try:
            with open(self.token_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_token(self, resp: dict) -> dict:
        token = {"access_token": resp["access_token"], "refresh_token": resp.get("refresh_token", ""),
                 "expires_at": self.wall_clock() + float(resp.get("expires_in", 0))}
        if self.token_path:
            tmp = self.token_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(token, f)
            os.replace(tmp, self.token_path)
        return token

    def _exchange(self, **fields) -> Optional[dict]:
        try:
            return self._save_token(self.post(TOKEN_URL, {"client_id": self.client_id,
                                                          "client_secret": self.client_secret, **fields}))
        except Exception as e:
            self._update(error=f"TOKEN: {type(e).__name__}: {e}")
            return None

    def _authenticate(self, allow_refresh: bool = True) -> None:
        token = self._load_token()
        if token.get("access_token") and token.get("expires_at", 0) > self.wall_clock() + 60:
            self._send("AUTHENTICATE", {"access_token": token["access_token"]},
                       lambda data, err: self._on_authenticated(data, err, allow_refresh))
        elif allow_refresh and token.get("refresh_token"):
            if self._exchange(grant_type="refresh_token", refresh_token=token["refresh_token"]):
                self._authenticate(allow_refresh=False)
            else:
                self._authorize()
        else:
            self._authorize()

    def _on_authenticated(self, data: dict, error: bool, allow_refresh: bool) -> None:
        if error:
            token = self._load_token()
            if allow_refresh and token.get("refresh_token") and \
                    self._exchange(grant_type="refresh_token", refresh_token=token["refresh_token"]):
                self._authenticate(allow_refresh=False)
            else:
                self._authorize()
            return
        user = data.get("user") or {}
        self._authed = True
        self._channel_at = 0.0
        self._update(state="CONNECTED", error="", user=user.get("global_name") or user.get("username") or "")
        for evt in ("VOICE_SETTINGS_UPDATE", "NOTIFICATION_CREATE", "VOICE_CHANNEL_SELECT"):
            self._send("SUBSCRIBE", {}, evt=evt)
        self._send("GET_VOICE_SETTINGS", {}, self._on_voice_settings)

    def _authorize(self) -> None:
        self._update(state="APPROVE IN DISCORD")
        self._send("AUTHORIZE", {"client_id": self.client_id, "scopes": SCOPES}, self._on_authorized)

    def _on_authorized(self, data: dict, error: bool) -> None:
        if error or not data.get("code"):
            self._update(state="NOT APPROVED", error=str(data.get("message") or "denied"))
            return
        if self._exchange(grant_type="authorization_code", code=data["code"], redirect_uri=self.redirect_uri):
            self._authenticate(allow_refresh=False)
        else:
            self._update(state="ERROR")

    # --- responses ---------------------------------------------------------------------

    def _on_voice_settings(self, data: dict, error: bool) -> None:
        if not error:
            self._update(mute=data.get("mute"), deaf=data.get("deaf"))

    def _on_channel(self, data: dict, error: bool) -> None:
        if not error:
            self._update(channel=parse_channel(data))

    def _expire_unseen(self) -> None:
        if self.snapshot["unseen"]:
            cutoff = self.wall_clock() - self.light_s
            fresh = sum(1 for n in self.snapshot["notifications"][:self.snapshot["unseen"]] if n["at"] >= cutoff)
            if fresh != self.snapshot["unseen"]:
                self._update(unseen=fresh)

    # --- actions (queued onto the service thread) ------------------------------------------

    def _when_connected(self, fn, *args) -> None:
        if self._authed and self.t is not None:
            try:
                fn(*args)
            except OSError as e:
                self._drop("DISCORD NOT RUNNING", str(e))

    def toggle_mute(self) -> None:
        self.do(self._when_connected, self._toggle, "mute")

    def toggle_deaf(self) -> None:
        self.do(self._when_connected, self._toggle, "deaf")

    def _toggle(self, key: str) -> None:
        self._send("SET_VOICE_SETTINGS", {key: not self.snapshot.get(key)}, self._on_voice_settings)

    def leave_channel(self) -> None:
        self.do(self._when_connected, self._leave)

    def _leave(self) -> None:
        self._send("SELECT_VOICE_CHANNEL", {"channel_id": None, "force": True}, lambda d, e: None)
        self._update(channel=None)

    def mark_seen(self) -> None:
        self.do(self._mark_seen)

    def _mark_seen(self) -> None:
        self._update(unseen=0)

    def clear_notifications(self) -> None:
        self.do(self._clear_notifications)

    def _clear_notifications(self) -> None:
        self._update(notifications=[], unseen=0)

    def reconnect(self) -> None:
        """Connect now (e.g. after approving, or once Discord is running)."""
        self.do(self._reconnect)

    def _reconnect(self) -> None:
        self._drop("CONNECTING")
        self._retry_at = 0.0
