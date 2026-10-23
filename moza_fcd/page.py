"""The 14x24 character grid the FCD renders, plus helpers for drawing on it."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Iterable, List, Union

from .protocol import MCDU_COLUMNS, MCDU_ROWS

ROWS = MCDU_ROWS
COLUMNS = MCDU_COLUMNS

# Style byte = bit7 (1 = large, 0 = small) | bits3..0 colour code. Codes 1-8 pick the device's text
# colours (MOZA Cockpit > Color Settings). Confirmed on an MB7F with Cockpit's defaults:
#   screen firmware 1.2.7.32+: codes 8-15 = Text Color 9 (grey, "disabled or unavailable options");
#   screen firmware <= 1.2.7.30: code 8 = Text Color 8 (black by design, so grey is invisible) and
#   codes 9-15 repeat colours 1-7. Code 0 draws nothing on both. On old firmware use remap_color("grey", "white").
# Bits 6..4 are unused by MobiFlight; their meaning (if any) is unknown.
COLOR_CODES: Dict[str, int] = {
    "cyan": 1,
    "red": 2,
    "yellow": 3,
    "green": 4,
    "magenta": 5,
    "amber": 6,
    "white": 7,
    "grey": 8,
}

# WinWing/MobiFlight CDU colour letters -> MOZA colour codes. 'o' (blue) and 'k' (khaki)
# have no dedicated slot and approximate to cyan and amber.
LETTER_CODES: Dict[str, int] = {
    "w": 7, "c": 1, "r": 2, "y": 3, "g": 4, "m": 5, "a": 6, "e": 8, "o": 1, "k": 6,
}

Color = Union[str, int]


def color_code(color: Color) -> int:
    """Accept a colour name ('amber'), a WinWing letter ('a') or a raw code (0-15)."""
    if isinstance(color, int):
        if not 0 <= color <= 0x0F:
            raise ValueError("Raw colour code must be 0-15.")
        return color
    c = color.lower()
    if c in COLOR_CODES:
        return COLOR_CODES[c]
    if c in LETTER_CODES:
        return LETTER_CODES[c]
    raise ValueError(f"Unknown colour {color!r}. Use one of {sorted(COLOR_CODES)} or a WinWing letter.")


DEFAULT_COLOR_CODES = dict(COLOR_CODES)
DEFAULT_LETTER_CODES = dict(LETTER_CODES)


def remap_color(name: str, to: Color) -> None:
    """Draw colour `name` with another palette slot from now on, e.g. remap_color("grey", "white") when
    the device's 8th text colour is black. Affects every Page, and the WinWing letters for that colour.
    remap_color("grey", "grey") restores the default."""
    name = name.lower()
    if name not in DEFAULT_COLOR_CODES:
        raise ValueError(f"Unknown colour {name!r}")
    if isinstance(to, str) and to.lower() in DEFAULT_COLOR_CODES:
        code = DEFAULT_COLOR_CODES[to.lower()]
    else:
        code = color_code(to)
    COLOR_CODES[name] = code
    for letter, default in DEFAULT_LETTER_CODES.items():
        if default == DEFAULT_COLOR_CODES[name]:
            LETTER_CODES[letter] = code


@dataclass(frozen=True)
class Cell:
    char: str = " "
    color: int = 7
    small: bool = False

    @property
    def style(self) -> int:
        return (0x00 if self.small else 0x80) | (self.color & 0x0F)


BLANK = Cell()


class Page:
    """A mutable 14x24 grid. Pass it to `FcdScreen.show()`; only changed rows are sent."""

    def __init__(self) -> None:
        self._cells: List[Cell] = [BLANK] * (ROWS * COLUMNS)

    def copy(self) -> "Page":
        p = Page()
        p._cells = list(self._cells)
        return p

    def __getitem__(self, rc) -> Cell:
        r, c = rc
        return self._cells[r * COLUMNS + c]

    def __setitem__(self, rc, cell: Cell) -> None:
        r, c = rc
        if 0 <= r < ROWS and 0 <= c < COLUMNS:
            self._cells[r * COLUMNS + c] = cell

    def clear(self) -> "Page":
        self._cells = [BLANK] * (ROWS * COLUMNS)
        return self

    def write(self, row: int, col: int, text: str, color: Color = "white", small: bool = False) -> "Page":
        """Write text starting at (row, col); anything past column 23 is clipped. Control characters
        (NUL, tab, newline...) become blanks: a NUL reaching the device stops that row updating."""
        code = color_code(color)
        for i, ch in enumerate(text):
            if ord(ch) < 32 or ord(ch) == 127:
                ch = " "
            self[row, col + i] = Cell(ch, code, small)
        return self

    def center(self, row: int, text: str, color: Color = "white", small: bool = False) -> "Page":
        text = text[:COLUMNS]
        return self.write(row, (COLUMNS - len(text)) // 2, text, color, small)

    def right(self, row: int, text: str, color: Color = "white", small: bool = False) -> "Page":
        text = text[:COLUMNS]
        return self.write(row, COLUMNS - len(text), text, color, small)

    def fill(self, row: int, char: str = " ", color: Color = "white", small: bool = False) -> "Page":
        return self.write(row, 0, char * COLUMNS, color, small)

    def lines(self, rows: Iterable[str], start_row: int = 0, color: Color = "white", small: bool = False) -> "Page":
        for i, text in enumerate(rows):
            self.write(start_row + i, 0, text, color, small)
        return self

    # --- conversion -----------------------------------------------------------------

    def row_text(self, row: int) -> str:
        return "".join(self[row, c].char for c in range(COLUMNS))

    def row_styles(self, row: int) -> bytes:
        return bytes(self[row, c].style for c in range(COLUMNS))

    def row_equals(self, other: "Page", row: int) -> bool:
        start = row * COLUMNS
        return self._cells[start : start + COLUMNS] == other._cells[start : start + COLUMNS]

    def to_text(self) -> str:
        return "\n".join(self.row_text(r) for r in range(ROWS))

    @classmethod
    def from_winwing_json(cls, data: Union[str, dict]) -> "Page":
        """Parse the WinWing/MobiFlight CDU format: {"Target":"Display","Data":[[char,colour,small],...]}.

        Empty cells may be []; the 4th element (inverted) is accepted but not rendered
        - no inverted style bit is known.
        """
        root = json.loads(data) if isinstance(data, str) else data
        cells = root.get("Data")
        if not isinstance(cells, list):
            raise ValueError('Missing "Data" array.')
        page = cls()
        for i, cell in enumerate(cells[: ROWS * COLUMNS]):
            if not cell:
                continue
            ch = str(cell[0])[:1] or " "
            letter = str(cell[1]) if len(cell) > 1 else "w"
            small = len(cell) > 2 and int(cell[2]) != 0
            page._cells[i] = Cell(ch, LETTER_CODES.get(letter, 7), small)
        return page
