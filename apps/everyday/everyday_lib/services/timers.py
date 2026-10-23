"""Countdown timer, stopwatch and Pomodoro. Pure logic with an injectable clock, so it's testable."""

from __future__ import annotations

import time
from typing import Callable, List, Optional


class Countdown:
    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.duration = 0.0
        self._ends_at: Optional[float] = None
        self._left = 0.0

    @property
    def running(self) -> bool:
        return self._ends_at is not None

    @property
    def is_set(self) -> bool:
        return self.duration > 0

    def set(self, seconds: float) -> None:
        self.duration = self._left = float(seconds)
        self._ends_at = None

    def start(self) -> None:
        if self._left > 0 and not self.running:
            self._ends_at = self.clock() + self._left

    def pause(self) -> None:
        if self.running:
            self._left = max(0.0, self._ends_at - self.clock())
            self._ends_at = None

    def reset(self) -> None:
        self._left, self._ends_at = self.duration, None

    def remaining(self) -> float:
        return max(0.0, self._ends_at - self.clock()) if self.running else self._left

    def check_done(self) -> bool:
        """True once, when a running countdown reaches zero."""
        if self.running and self.clock() >= self._ends_at:
            self._ends_at, self._left = None, 0.0
            return True
        return False


class Stopwatch:
    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self._started: Optional[float] = None
        self._acc = 0.0

    @property
    def running(self) -> bool:
        return self._started is not None

    def toggle(self) -> None:
        if self.running:
            self._acc += self.clock() - self._started
            self._started = None
        else:
            self._started = self.clock()

    def reset(self) -> None:
        self._started, self._acc = None, 0.0

    def elapsed(self) -> float:
        return self._acc + (self.clock() - self._started if self.running else 0.0)


class Pomodoro:
    def __init__(self, clock: Callable[[], float], focus_min: float = 25, break_min: float = 5) -> None:
        self.clock = clock
        self.focus_s, self.break_s = focus_min * 60, break_min * 60
        self.phase = "FOCUS"
        self.count = 0           # completed focus sessions
        self.timer = Countdown(clock)
        self.timer.set(self.focus_s)

    @property
    def running(self) -> bool:
        return self.timer.running

    def toggle(self) -> None:
        self.timer.pause() if self.running else self.timer.start()

    def skip(self) -> None:
        was_running = self.running
        self._next_phase()
        if was_running:
            self.timer.start()

    def stop(self) -> None:
        self.phase, self.count = "FOCUS", 0
        self.timer.set(self.focus_s)

    def _next_phase(self) -> None:
        if self.phase == "FOCUS":
            self.count += 1
            self.phase = "BREAK"
            self.timer.set(self.break_s)
        else:
            self.phase = "FOCUS"
            self.timer.set(self.focus_s)

    def check_done(self) -> Optional[str]:
        """Returns the new phase when one ends (and starts the next one automatically)."""
        if self.timer.check_done():
            self._next_phase()
            self.timer.start()
            return self.phase
        return None


class Timers:
    """All timers plus the alarm state that drives the CALL light."""

    def __init__(self, focus_min: float = 25, break_min: float = 5, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.countdown = Countdown(clock)
        self.stopwatch = Stopwatch(clock)
        self.pomodoro = Pomodoro(clock, focus_min, break_min)
        self.alarm: Optional[str] = None   # message while an alarm is waiting to be acknowledged

    def tick(self) -> List[str]:
        """Check for finished timers; returns new alarm messages."""
        fired = []
        if self.countdown.check_done():
            fired.append("TIMER DONE: ANY KEY")
        phase = self.pomodoro.check_done()
        if phase:
            fired.append(f"{phase} TIME: ANY KEY")
        if fired:
            self.alarm = fired[-1]
        return fired

    def acknowledge(self) -> bool:
        if self.alarm:
            self.alarm = None
            return True
        return False
