"""Windows controls: lock, monitors, sleep, scheduled restart/shutdown, keep-awake, power plans, dark
mode, plus the launcher's actions (open, key macros, typed text). All actions run on this service's
thread, which also keeps the keep-awake request alive (Windows ties it to the calling thread)."""

from __future__ import annotations

import sys
import time

from . import Worker
from .. import winapi


class WindowsService(Worker):
    name = "windows"
    interval = 15.0

    def __init__(self, api=winapi, wall_clock=time.time) -> None:
        super().__init__()
        self.api = api
        self.wall_clock = wall_clock
        self._keep_awake = False
        self._pending = None            # {"kind": "shutdown"|"restart", "at": wall time}
        self._failed = None             # (message, wall time) of the last failed launch
        self.snapshot = {"plans": [], "keep_awake": False, "dark": None, "pending": None}

    def setup(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windows only")

    def poll(self) -> None:
        try:
            plans = self.api.power_plans()
        except Exception:
            plans = self.snapshot.get("plans", [])
        if self._pending and self.wall_clock() > self._pending["at"] + 5:
            self._pending = None
        failed = self._failed[0] if self._failed and self.wall_clock() - self._failed[1] < 5 else None
        self.snapshot = {"plans": plans, "keep_awake": self._keep_awake, "dark": self.api.dark_mode(),
                         "pending": dict(self._pending) if self._pending else None, "failed": failed}

    def teardown(self) -> None:
        if self._keep_awake:
            self.api.set_keep_awake(False)

    # --- actions (queued onto the service thread) ------------------------------------

    def lock(self) -> None:
        self.do(self.api.lock)

    def monitors_off(self) -> None:
        self.do(self.api.monitors_off)

    def sleep(self) -> None:
        self.do(self.api.suspend)

    def shutdown(self, kind: str, delay_s: int) -> None:
        self.do(self._shutdown, kind, delay_s)

    def _shutdown(self, kind: str, delay_s: int) -> None:
        self.api.schedule_shutdown(kind, delay_s)
        self._pending = {"kind": kind, "at": self.wall_clock() + delay_s}

    def abort_shutdown(self) -> None:
        self.do(self._abort)

    def _abort(self) -> None:
        self.api.abort_shutdown()
        self._pending = None

    def toggle_keep_awake(self) -> None:
        self.do(self._set_keep_awake, not self._keep_awake)

    def _set_keep_awake(self, on: bool) -> None:
        self.api.set_keep_awake(on)
        self._keep_awake = on

    def set_power_plan(self, guid: str) -> None:
        self.do(self.api.set_power_plan, guid)

    def toggle_dark_mode(self) -> None:
        self.do(self._toggle_dark)

    def _toggle_dark(self) -> None:
        current = self.api.dark_mode()
        self.api.set_dark_mode(not current)

    def launch(self, run: str, args=()) -> None:
        self.do(self._try, "OPEN FAILED", self.api.launch, run, tuple(args))

    def _try(self, message: str, fn, *args) -> None:
        """Run an action; on failure, report `message` in snapshot["failed"] for a few seconds."""
        try:
            fn(*args)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self._failed = (message, self.wall_clock())

    def send_keys(self, steps) -> None:
        self.do(self.api.run_steps, list(steps))

    def type_text(self, text: str) -> None:
        self.do(self.api.type_text, text)
