"""A small live dashboard (clock, disk, uptime, ticker) using only the standard library.

    python -m moza_fcd gui --app examples/dashboard.py     # in the emulator
    python -m moza_fcd run --app examples/dashboard.py     # on the real device
    python examples/dashboard.py sim                       # standalone, drawn in the terminal
"""

import platform
import shutil
import sys
import time
from typing import Callable, Optional

sys.path.insert(0, __file__.rsplit("examples", 1)[0])

from moza_fcd import COLUMNS, FcdScreen, Page

TICKER = "  MOZA FCD PYTHON DRIVER - WRITE YOUR OWN PAGES - 14 ROWS X 24 COLUMNS - 9 COLOURS  "


def build(t0: float) -> Page:
    now = time.time()
    pg = Page()
    pg.center(0, "SYSTEM", "cyan")

    pg.write(1, 1, "TIME", small=True).right(1, "DATE ", small=True)
    pg.write(2, 0, time.strftime(" %H:%M:%S"), "green").right(2, time.strftime("%a %d %b ").upper(), "green")

    total, used, _free = shutil.disk_usage("/")
    pct = used / total
    pg.write(4, 1, "DISK", small=True).right(4, f"{used / 2**30:.0f}/{total / 2**30:.0f} GB ", small=True)
    filled = int(pct * 22)
    pg.write(5, 1, "#" * filled, "red" if pct > 0.9 else "amber" if pct > 0.75 else "green")
    pg.write(5, 1 + filled, "." * (22 - filled), "grey")

    pg.write(7, 1, "HOST", small=True)
    pg.write(8, 1, platform.node()[:22].upper(), "white")

    up = int(now - t0)
    pg.write(10, 1, "SESSION", small=True).right(10, f"{up // 60:02d}:{up % 60:02d} ", "magenta")

    offset = int(now * 4) % len(TICKER)
    pg.write(12, 0, (TICKER[offset:] + TICKER)[:COLUMNS], "yellow", small=True)
    return pg


def run_loop(screen: FcdScreen, on_frame: Optional[Callable[[], None]] = None) -> None:
    t0 = time.time()
    while True:
        screen.show(build(t0))  # only changed rows go over the wire
        if on_frame:
            on_frame()
        time.sleep(0.25)


def main(screen: FcdScreen) -> None:
    run_loop(screen)


if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else None  # None = auto-detect, "sim", or "COM7"
    with FcdScreen(port=port, on_error=print) as screen:
        if not screen.wait_ready():
            sys.exit(f"Device not ready (state={screen.state.value})")
        render = None
        if screen.simulator:
            render = lambda: sys.stdout.write("\x1b[H\x1b[2J" + screen.simulator.render_ansi() + "\n")
        try:
            run_loop(screen, render)
        except KeyboardInterrupt:
            screen.set_display_mode(0)  # hand the screen back to the device's own UI
            time.sleep(0.3)
