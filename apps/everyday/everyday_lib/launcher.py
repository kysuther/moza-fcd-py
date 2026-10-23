"""Launcher items from config.toml [launcher] items.

Each item has a label and one action:
    run  = "explorer.exe" / "ms-settings:" / "https://..." / a file or folder   (args = [...] optional)
    keys = "WIN+SHIFT+S" or a list of steps: ["CTRL+C", "WAIT 0.5", "ALT+TAB"]
    text = "typed into the focused window"
Optional: confirm = true  (needs EXEC before it runs)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import winapi
from .util import clean

LABEL_WIDTH = 11       # two labels plus "<" and ">" fit one 24-column line


@dataclass
class LauncherItem:
    label: str
    kind: str                       # "run", "keys", "text" or "none"
    run: str = ""
    args: List[str] = field(default_factory=list)
    steps: list = field(default_factory=list)
    text: str = ""
    confirm: bool = False
    error: Optional[str] = None     # set when the config entry can't be used

    @property
    def kind_label(self) -> str:
        if self.error:
            return self.error
        return {"run": "OPEN", "keys": "KEYS", "text": "TYPE"}.get(self.kind, "")


def parse_items(raw_items) -> List[LauncherItem]:
    items = []
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            continue
        label = clean(raw.get("label"), LABEL_WIDTH) or "?"
        confirm = bool(raw.get("confirm", False))
        if raw.get("run"):
            args = raw.get("args") or []
            items.append(LauncherItem(label, "run", run=str(raw["run"]),
                                      args=[str(a) for a in (args if isinstance(args, list) else [args])],
                                      confirm=confirm))
        elif raw.get("keys"):
            try:
                items.append(LauncherItem(label, "keys", steps=winapi.parse_steps(raw["keys"]), confirm=confirm))
            except ValueError:
                items.append(LauncherItem(label, "keys", confirm=confirm, error="BAD KEYS"))
        elif raw.get("text"):
            items.append(LauncherItem(label, "text", text=str(raw["text"]), confirm=confirm))
        else:
            items.append(LauncherItem(label, "none", error="NO ACTION"))
    return items
