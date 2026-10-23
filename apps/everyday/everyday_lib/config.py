"""config.toml (hand-edited) and settings.json (changed on the FCD) with defaults."""

from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Dict

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    tomllib = None

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(APP_DIR, "config.toml")
EXAMPLE_CONFIG_PATH = os.path.join(APP_DIR, "config.example.toml")   # committed; config.toml is not
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "clock": {"world": [{"zone": "UTC", "label": "UTC"}]},
    "timers": {"pomodoro_focus_min": 25, "pomodoro_break_min": 5},
    "system": {"ping_host": "1.1.1.1"},
    "alerts": {"cpu_percent": 95, "cpu_seconds": 30, "ram_percent": 90, "gpu_temp_c": 85, "disk_free_percent": 10},
    "audio": {"volume_step": 5, "mixer_hide": [], "mic_mute_light": True},
    "display": {"grey": "grey"},
    "windows": {"shutdown_delay_s": 60},
    "launcher": {"items": [
        {"label": "EXPLORER", "run": "explorer.exe"},
        {"label": "TASK MGR", "run": "taskmgr.exe"},
        {"label": "SETTINGS", "run": "ms-settings:"},
        {"label": "DOWNLOADS", "run": "%USERPROFILE%\\Downloads"},
        {"label": "SNIP", "keys": "WIN+SHIFT+S"},
    ]},
    "weather": {"city": "Chicago", "latitude": 41.88, "longitude": -87.63, "metar_airport": "KORD",
                "units": "imperial"},
    "discord": {"client_id": "", "client_secret": "", "redirect_uri": "http://localhost",
                "notification_light_minutes": 10},
    "simbrief": {"userid": "", "username": ""},
}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "start_page": "menu",
    "idle_minutes": 10,      # dim the screen after this long without PC or FCD input; 0 = never
    "idle_screen": 10,       # screen brightness % while dimmed
    "auto_brightness": None, # device photoresistor switch as last set from the app (None = not set yet)
}
IDLE_MINUTES_CHOICES = (0, 2, 5, 10, 15, 30, 60)
IDLE_SCREEN_CHOICES = (0, 5, 10, 20, 30)

START_PAGES = ("menu", "dashboard", "audio", "media", "system", "clock", "launcher", "tools", "windows",
               "weather", "simbrief", "discord")


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    if tomllib is None or not os.path.exists(path):
        return copy.deepcopy(DEFAULT_CONFIG)
    with open(path, "rb") as f:
        return _merge(DEFAULT_CONFIG, tomllib.load(f))


class Settings:
    """Small persisted key/value store for things changed on the device."""

    def __init__(self, path: str = SETTINGS_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.data = copy.deepcopy(DEFAULT_SETTINGS)
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data.update(json.load(f))
            except (OSError, ValueError):
                pass

    def __getitem__(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value) -> None:
        with self._lock:
            self.data[key] = value
            if self.path:
                try:
                    with open(self.path, "w", encoding="utf-8") as f:
                        json.dump(self.data, f, indent=2)
                except OSError:
                    pass
