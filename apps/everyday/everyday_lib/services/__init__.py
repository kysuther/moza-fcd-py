"""Background services. Each polls on its own thread into `snapshot` (a dict replaced atomically, so
pages can read it at any time without locks) and runs actions queued with `do()` on that same
thread - which matters for COM-based APIs like Windows audio."""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable, Dict


class Worker:
    name = "worker"
    interval = 1.0

    def __init__(self) -> None:
        self.snapshot: Dict[str, Any] = {}
        self.available = True       # False when the underlying library/OS feature is missing
        self.error = ""
        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = None

    # --- lifecycle -----------------------------------------------------------------

    def start(self) -> "Worker":
        self._thread = threading.Thread(target=self._run, name=f"Everyday-{self.name}", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            self.setup()
        except Exception as e:  # library missing, etc.
            self.available, self.error = False, f"{type(e).__name__}: {e}"
            return
        next_poll = 0.0
        while not self._stop.is_set():
            timeout = max(0.0, next_poll - time.monotonic())
            try:
                fn, args = self._q.get(timeout=timeout)
                try:
                    fn(*args)
                except Exception as e:
                    self.error = f"{type(e).__name__}: {e}"
                next_poll = 0.0           # refresh right after an action so the screen updates
                continue
            except queue.Empty:
                pass
            try:
                self.poll()
                self.error = ""
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
            next_poll = time.monotonic() + self.interval
        self.teardown()

    # --- for subclasses ------------------------------------------------------------

    def setup(self) -> None: ...
    def poll(self) -> None: ...
    def teardown(self) -> None: ...

    def do(self, fn: Callable, *args) -> None:
        """Run fn(*args) on the service thread."""
        self._q.put((fn, args))
