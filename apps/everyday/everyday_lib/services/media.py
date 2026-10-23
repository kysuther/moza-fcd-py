"""Now playing (Windows media session, via the nowplaying.ps1 helper) and media keys."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

from . import Worker

HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nowplaying.ps1")
VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_PLAY_PAUSE = 0xB0, 0xB1, 0xB3
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x1, 0x2


def press_media_key(vk: int) -> None:
    """Send a media key, the same as a keyboard's play/pause/next/previous keys."""
    import ctypes
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)


class MediaService(Worker):
    name = "media"
    interval = 5.0  # only used to restart the helper if it died

    def __init__(self, key_sender=press_media_key) -> None:
        super().__init__()
        self.key_sender = key_sender
        self._proc = None
        self._received_at = 0.0
        self.snapshot = {"active": False}

    def setup(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windows only")

    def poll(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            self._start_helper()

    def teardown(self) -> None:
        self._kill_helper()

    def stop(self) -> None:
        super().stop()
        self._kill_helper()  # don't wait for the worker loop: the helper must not outlive the app

    def _kill_helper(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            proc.kill()

    def _start_helper(self) -> None:
        self._proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", HELPER, "-IntervalMs", "1000",
             "-ParentPid", str(os.getpid())],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._read, args=(self._proc,), name="Everyday-media-reader", daemon=True).start()

    def _read(self, proc) -> None:
        for line in proc.stdout:
            self.feed(line)

    def feed(self, line: str, now: float = None) -> None:
        """Parse one helper line into the snapshot (also used by tests)."""
        try:
            data = json.loads(line)
        except ValueError:
            return
        data["received_at"] = time.monotonic() if now is None else now
        self.snapshot = data

    def position(self, now: float = None) -> float:
        """Playback position, advanced locally between helper updates while playing."""
        s = self.snapshot
        pos = float(s.get("position") or 0)
        if s.get("status") == "Playing" and s.get("duration"):
            pos += (time.monotonic() if now is None else now) - s.get("received_at", 0)
            pos = min(pos, float(s["duration"]))
        return pos

    # --- actions (sent immediately: keybd_event is thread-safe) ------------------------

    def play_pause(self) -> None:
        self.key_sender(VK_MEDIA_PLAY_PAUSE)

    def next(self) -> None:
        self.key_sender(VK_MEDIA_NEXT)

    def previous(self) -> None:
        self.key_sender(VK_MEDIA_PREV)
