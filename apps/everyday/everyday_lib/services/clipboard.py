"""Clipboard text and a short history of recent text clips. The history lives in memory only and is
gone when the app stops."""

from __future__ import annotations

import sys

from . import Worker
from .. import winapi

HISTORY = 10


class ClipboardService(Worker):
    name = "clipboard"
    interval = 0.5

    def __init__(self, api=winapi) -> None:
        super().__init__()
        self.api = api
        self._seq = None
        self.snapshot = {"text": "", "history": []}

    def setup(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windows only")

    def poll(self) -> None:
        seq = self.api.clipboard_sequence()
        if seq == self._seq:
            return
        text = self.api.get_clipboard_text()
        if text is None:                 # couldn't open it; try again next poll
            return
        self._seq = seq
        history = list(self.snapshot["history"])
        if text.strip():
            history = [text] + [h for h in history if h != text]
        self.snapshot = {"text": text, "history": history[:HISTORY]}

    def set_text(self, text: str) -> None:
        self.do(self.api.set_clipboard_text, text)

    def clear(self) -> None:
        self.do(self.api.clear_clipboard)
