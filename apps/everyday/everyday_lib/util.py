"""Text helpers for the 24-column, ASCII-only screen."""

from __future__ import annotations

import re
import unicodedata


def clean(text, width: int = 24) -> str:
    """Upper-case printable ASCII (plus the degree sign), emoji and accents removed, spaces collapsed."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(ch for ch in s if ch == "°" or (32 <= ord(ch) < 127))
    s = re.sub(r"\s+", " ", s).strip().upper()
    return s[:width]


def wrap2(text: str, width: int = 24):
    """Split into at most two lines at a word boundary when possible."""
    if len(text) <= width:
        return text, ""
    cut = text.rfind(" ", 0, width + 1)
    if cut <= width // 2:
        cut = width
    return text[:cut].rstrip(), text[cut:].strip()[:width]


def wrap_lines(text: str, width: int = 24, max_lines: int = 8):
    """Word-wrap cleaned text into at most max_lines lines (long words are split)."""
    words, lines, cur = clean(text, 10_000).split(" "), [], ""
    for w in words:
        while len(w) > width:
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(w[:width])
            w = w[width:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:max_lines]


def bar(percent: float, width: int = 10) -> str:
    """'######----' style bar."""
    p = max(0.0, min(100.0, float(percent or 0)))
    filled = int(round(p / 100 * width))
    return "#" * filled + "-" * (width - filled)


def mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds or 0)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_duration(entry: str):
    """'25' -> 1500 (minutes), '1:30' -> 90 (m:ss), '90S' -> 90. None if invalid or zero."""
    e = entry.strip().upper()
    m = re.fullmatch(r"(\d{1,3})", e)
    if m:
        secs = int(m.group(1)) * 60
    elif re.fullmatch(r"(\d{1,3}):([0-5]\d)", e):
        mins, s = e.split(":")
        secs = int(mins) * 60 + int(s)
    elif re.fullmatch(r"(\d{1,5})S", e):
        secs = int(e[:-1])
    else:
        return None
    return secs if 0 < secs <= 24 * 3600 else None


def parse_percent(entry: str):
    e = entry.strip()
    if re.fullmatch(r"\d{1,3}", e) and 0 <= int(e) <= 100:
        return int(e)
    return None


def rate(bytes_per_s: float) -> str:
    """Bytes/s as MB/s with one decimal (or KB/s below 0.1 MB/s)."""
    v = float(bytes_per_s or 0)
    if v >= 100_000:
        return f"{v / 1e6:.1f}M"
    return f"{v / 1e3:.0f}K"


def duration_words(seconds: float) -> str:
    """Uptime style: '3D 04:12' / '04:12'."""
    seconds = int(seconds or 0)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}D {h:02d}:{m:02d}" if d else f"{h:02d}:{m:02d}"
