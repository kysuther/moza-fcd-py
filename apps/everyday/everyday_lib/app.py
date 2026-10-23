"""The everyday app: CduApp plus services, alerts and the status lights.

Lights:  CALL  timer/Pomodoro alarm (flashes until any key is pressed)
         FAIL  microphone muted (config audio.mic_mute_light)
         MSG   an alert is active (see config [alerts]), a scratchpad message is showing, or a Discord
               notification arrived (until you open DISCORD > NOTIFICATIONS, or it times out)
         EXEC  a confirmation is waiting (e.g. END PROCESS)
         OFST  reserved (Do Not Disturb, later phase)

Idle dimming: after settings idle_minutes without PC or FCD input, the screen drops to idle_screen %;
any input brings it back (an FCD key press that wakes it does nothing else).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from moza_fcd.app import CduApp

from .config import Settings
from .services.timers import Timers

SCREEN_STEP = 10      # percent, BRT -/+ and the settings page
LEVEL_STEP = 1        # key backlight / annunciators, 0-15


@dataclass
class Context:
    config: Dict[str, Any]
    settings: Settings
    audio: Any
    media: Any
    system: Any
    timers: Timers
    windows: Any = None          # WindowsService (also runs the launcher's actions)
    clipboard: Any = None        # ClipboardService
    notes: Any = None            # NotesStore
    weather: Any = None          # WeatherService (forecast + METAR/TAF)
    simbrief: Any = None         # SimBriefService
    discord: Any = None          # DiscordService
    clock: Any = time.monotonic
    pc_idle: Any = lambda: 0.0   # seconds since the last PC keyboard/mouse input (winapi.idle_seconds)
    cpu_high_since: Optional[float] = None
    alerts: List[str] = field(default_factory=list)
    # Device brightness as last read/set: screen 0-100 %, keys and lamps 0-15 (None = not known yet).
    levels: Dict[str, Optional[int]] = field(default_factory=lambda: {"screen": None, "keys": None, "lamps": None})


def compute_alerts(ctx: Context) -> List[str]:
    """Alert texts (max 24 chars) for the MSG light, from the system snapshot and config thresholds."""
    a = ctx.config["alerts"]
    s = ctx.system.snapshot
    out = []
    cpu = s.get("cpu")
    if cpu is not None and cpu >= a["cpu_percent"]:
        if ctx.cpu_high_since is None:
            ctx.cpu_high_since = ctx.clock()
        if ctx.clock() - ctx.cpu_high_since >= a["cpu_seconds"]:
            out.append(f"CPU HIGH {cpu:.0f}%")
    else:
        ctx.cpu_high_since = None
    if s.get("ram") is not None and s["ram"] >= a["ram_percent"]:
        out.append(f"RAM HIGH {s['ram']:.0f}%")
    gpu = s.get("gpu")
    if gpu and gpu.get("temp") is not None and gpu["temp"] >= a["gpu_temp_c"]:
        out.append(f"GPU HOT {gpu['temp']:.0f}°C")
    for d in s.get("disks", []):
        if d["free_pct"] <= a["disk_free_percent"]:
            out.append(f"DISK {d['name']} LOW {d['free_pct']:.0f}%")
    return out


class EverydayApp(CduApp):
    def __init__(self, screen, ctx: Context, home: str, function_keys: Dict[str, str]) -> None:
        super().__init__(screen, home=home, function_keys=function_keys, refresh_s=0.5)
        self.ctx = ctx
        self._light_state: Dict[str, bool] = {}
        self._last_key_at = ctx.clock()
        self.dimmed = False              # screen dimmed for idle; ctx.levels["screen"] keeps the normal level
        self._ambient = None             # last light reading seen (auto-brightness check)
        self._sensor_set_at = -1e9       # when the app last sent the photoresistor switch

    # --- lights ----------------------------------------------------------------------

    def _set_light(self, name: str, on: bool) -> None:
        if self._light_state.get(name) != on and self.screen.set_light(name, on):
            self._light_state[name] = on

    def _update_lights(self) -> None:
        ctx = self.ctx
        alarm = ctx.timers.alarm
        self._set_light("EXEC", self.exec_pending)
        other_message = bool(self.message) and self.message != alarm
        discord_new = bool(ctx.discord and ctx.discord.snapshot.get("unseen"))
        self._set_light("MSG", other_message or bool(ctx.alerts) or discord_new)
        self._set_light("CALL", bool(alarm) and int(ctx.clock() * 2) % 2 == 0)
        mic_light = ctx.config["audio"].get("mic_mute_light", True)
        self._set_light("FAIL", bool(mic_light and ctx.audio.snapshot.get("mic_muted")))
        self._set_light("OFST", False)

    def lights_off(self) -> None:
        for name in ("EXEC", "MSG", "CALL", "FAIL", "OFST"):
            self.screen.set_light(name, False)
        self._light_state.clear()

    def refresh(self) -> None:
        with self._lock:
            ctx = self.ctx
            fired = ctx.timers.tick()
            if fired:
                self.message = fired[-1]
            ctx.alerts = compute_alerts(ctx)
            self._idle_tick()
            self._sensor_tick()
            self.screen.show(self.render())
            self._update_lights()

    # --- idle dimming (screen brightness only: two writes per idle period) ----------------

    def idle_for(self) -> float:
        """Seconds without PC keyboard/mouse input or an FCD key press."""
        try:
            pc = float(self.ctx.pc_idle())
        except Exception:
            pc = 0.0
        return min(pc, self.ctx.clock() - self._last_key_at)

    def _idle_tick(self) -> None:
        minutes = self.ctx.settings["idle_minutes"] or 0
        normal = self.ctx.levels.get("screen")
        if self.dimmed:
            if not minutes or self.idle_for() < 2 or self.ctx.timers.alarm:
                self.wake()
        elif minutes and normal is not None and not self.ctx.timers.alarm and self.idle_for() >= minutes * 60:
            dim = min(int(self.ctx.settings["idle_screen"]), normal)
            if dim < normal:
                self.screen.set_screen_brightness(dim)
                self.dimmed = True

    # --- auto brightness (the device's photoresistor switch) --------------------------------

    SENSOR_GRACE_S = 3.0                 # a reading can still arrive just after switching off

    def set_auto_brightness(self, on: bool) -> bool:
        if not self.screen.enable_light_sensor(on):
            return False
        self._sensor_set_at = self.ctx.clock()
        self.ctx.settings.set("auto_brightness", on)
        return True

    def _sensor_tick(self) -> None:
        """While off, the device's light readings freeze. So if they change while the app thinks the
        switch is off (or unknown), it was turned on elsewhere (e.g. in MOZA Cockpit): show ON.
        The other direction can't be detected - frozen readings look like steady light."""
        value = getattr(self.screen, "light_levels", {}).get("LIGHT_SENSOR_AB")
        previous, self._ambient = self._ambient, value
        if value is None or previous is None or value == previous:
            return
        if self.ctx.settings["auto_brightness"] is not True and \
                self.ctx.clock() - self._sensor_set_at > self.SENSOR_GRACE_S:
            self.ctx.settings.set("auto_brightness", True)

    def wake(self) -> None:
        """Back to the normal screen brightness (also called when the app exits)."""
        if self.dimmed:
            self.dimmed = False
            normal = self.ctx.levels.get("screen")
            if normal is not None:
                self.screen.set_screen_brightness(normal)

    # --- keys ------------------------------------------------------------------------

    def handle_key(self, name: str, pressed: bool) -> None:
        if pressed:
            self._last_key_at = self.ctx.clock()
            if self.dimmed:
                with self._lock:
                    self.wake()
                self.refresh()
                return                   # the waking press does nothing else (the screen was dim)
        if pressed and self.ctx.timers.alarm:
            with self._lock:
                alarm = self.ctx.timers.alarm
                self.ctx.timers.acknowledge()
                if self.message == alarm:
                    self.message = ""
            self.refresh()
            return                       # the acknowledging press does nothing else
        if name in ("BRT-", "BRT+"):
            if pressed:
                self.step_level("screen", -SCREEN_STEP if name == "BRT-" else SCREEN_STEP)
            return
        super().handle_key(name, pressed)

    # --- brightness (the device stores all three itself) -------------------------------

    LIMITS = {"screen": 100, "keys": 15, "lamps": 15}
    LABELS = {"screen": "SCREEN", "keys": "KEYS", "lamps": "LAMPS"}

    def read_levels(self) -> None:
        """Ask the device for its current brightness levels (blocks briefly; call off the key thread)."""
        levels = self.ctx.levels
        try:
            levels["screen"] = self.screen.screen_brightness()
            levels["keys"] = self.screen.key_backlight()
            levels["lamps"] = self.screen.indicator_brightness()
        except Exception:
            pass
        self.refresh()

    def set_level(self, which: str, value: int) -> None:
        value = max(0, min(self.LIMITS[which], int(value)))
        if which == "screen":
            self.screen.set_screen_brightness(value)
        elif which == "keys":
            self.screen.set_key_backlight(value)
        else:
            self.screen.set_indicator_brightness(value)
        self.ctx.levels[which] = value
        unit = "%" if which == "screen" else "/15"
        self.show_message(f"{self.LABELS[which]} BRIGHTNESS {value}{unit}")

    def step_level(self, which: str, delta: int) -> None:
        current = self.ctx.levels.get(which)
        if current is None:
            current = 50 if which == "screen" else 8
        self.set_level(which, current + delta)
