"""Calculator and unit conversions, sized for the CDU keypad.

The keypad has digits, '.', '/' and the +/- key (types '-', press again for '+'). 'X' multiplies.
An entry that starts with an operator continues from the previous answer: '+5', 'X2', '/4', '-1'.
"""

from __future__ import annotations

import math
import re
from typing import Callable, List, Optional, Tuple


class CalcError(ValueError):
    """Message fits the scratchpad."""


TOKEN = re.compile(r"\d+\.?\d*|\.\d+|[+\-X/]")


def _tokens(expr: str) -> List[str]:
    pos, out = 0, []
    for m in TOKEN.finditer(expr):
        if m.start() != pos:
            raise CalcError("INVALID ENTRY")
        out.append(m.group())
        pos = m.end()
    if pos != len(expr):
        raise CalcError("INVALID ENTRY")
    return out


def evaluate(expr: str, ans: Optional[float] = None) -> float:
    e = (expr or "").replace(" ", "").upper()
    if not e:
        if ans is None:
            raise CalcError("ENTER A CALCULATION")
        return ans
    toks = _tokens(e)
    if toks[0] in "+-X/":
        if ans is not None:
            toks.insert(0, "ANS")        # continue from the previous answer
        elif toks[0] != "-":
            raise CalcError("INVALID ENTRY")
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]

    def factor():
        t = peek()
        if t is None:
            raise CalcError("INVALID ENTRY")
        if t == "-":
            take()
            return -factor()
        if t in "+X/":
            raise CalcError("INVALID ENTRY")
        take()
        return float(ans) if t == "ANS" else float(t)

    def term():
        v = factor()
        while peek() in ("X", "/"):
            op = take()
            rhs = factor()
            if op == "X":
                v *= rhs
            elif rhs == 0:
                raise CalcError("DIVIDE BY ZERO")
            else:
                v /= rhs
        return v

    v = term()
    while peek() in ("+", "-"):
        op = take()
        rhs = term()
        v = v + rhs if op == "+" else v - rhs
    if pos != len(toks):
        raise CalcError("INVALID ENTRY")
    if math.isinf(v) or math.isnan(v):
        raise CalcError("OUT OF RANGE")
    return v


def fmt(v: float, width: int = 12) -> str:
    """Up to 10 significant digits, no trailing zeros, scientific when it wouldn't fit."""
    if v == 0:
        return "0"
    s = f"{v:.10G}"
    if "E" not in s and len(s) <= width:
        return s
    return f"{v:.4E}".replace("E+0", "E").replace("E-0", "E-").replace("E+", "E")


# --- unit conversions --------------------------------------------------------------------

def _linear(factor: float) -> Tuple[Callable[[float], float], Callable[[float], float]]:
    return (lambda x: x * factor), (lambda x: x / factor)


# (unit a, unit b, a -> b, b -> a). Five per page on the converter.
CONVERSIONS = [
    ("°F", "°C", lambda f: (f - 32) * 5 / 9, lambda c: c * 9 / 5 + 32),
    ("LB", "KG", *_linear(0.45359237)),
    ("MI", "KM", *_linear(1.609344)),
    ("FT", "M", *_linear(0.3048)),
    ("IN", "CM", *_linear(2.54)),
    ("GAL", "L", *_linear(3.785411784)),
    ("KT", "KM/H", *_linear(1.852)),
    ("KT", "MPH", *_linear(1.150779448)),
    ("NM", "KM", *_linear(1.852)),
    ("INHG", "HPA", *_linear(33.8638866667)),
]


def convert(index: int, reverse: bool, value: float) -> Tuple[str, str, float]:
    """-> (from unit, to unit, result)."""
    a, b, ab, ba = CONVERSIONS[index]
    return (b, a, ba(value)) if reverse else (a, b, ab(value))


def round_for_display(v: float) -> float:
    """Converter results: 2 decimals, or 4 for small numbers."""
    return round(v, 4 if abs(v) < 1 else 2)
