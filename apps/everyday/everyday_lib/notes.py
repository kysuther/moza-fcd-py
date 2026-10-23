"""Short notes typed on the FCD (one scratchpad line each), saved to notes.json next to config.toml."""

from __future__ import annotations

import json
import os
import threading
from typing import List

from .config import APP_DIR

NOTES_PATH = os.path.join(APP_DIR, "notes.json")
MAX_NOTES = 25


class NotesStore:
    def __init__(self, path: str = NOTES_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.notes: List[str] = []
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self.notes = [str(n)[:24] for n in data if str(n).strip()][:MAX_NOTES]
            except (OSError, ValueError, TypeError):
                pass

    def __len__(self) -> int:
        return len(self.notes)

    def add(self, text: str) -> bool:
        if len(self.notes) >= MAX_NOTES or not text.strip():
            return False
        self.notes.append(text[:24])
        self._save()
        return True

    def replace(self, index: int, text: str) -> None:
        self.notes[index] = text[:24]
        self._save()

    def delete(self, index: int) -> None:
        del self.notes[index]
        self._save()

    def _save(self) -> None:
        if not self.path:
            return
        with self._lock:
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.notes, f, indent=2)
                os.replace(tmp, self.path)
            except OSError:
                pass
