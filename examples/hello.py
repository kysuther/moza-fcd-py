"""Minimal example.

    python -m moza_fcd gui --app examples/hello.py     # in the emulator
    python -m moza_fcd run --app examples/hello.py     # on the real device
    python examples/hello.py sim                       # standalone, drawn in the terminal
"""

import sys
import time

sys.path.insert(0, __file__.rsplit("examples", 1)[0])  # run from a checkout without installing

from moza_fcd import FcdScreen, Page


def main(screen: FcdScreen) -> None:
    page = Page()
    page.center(5, "HELLO FROM PYTHON", "green")
    page.center(7, "MOZA FCD", "cyan", small=True)
    screen.show(page)  # the page stays up after main() returns


if __name__ == "__main__":
    port = sys.argv[1] if len(sys.argv) > 1 else None  # None = auto-detect, "sim", or "COM7"
    with FcdScreen(port=port, on_error=print) as screen:
        if not screen.wait_ready():
            sys.exit(f"Device not ready (state={screen.state.value})")
        main(screen)
        if screen.simulator:
            time.sleep(0.5)
            print(screen.simulator.render_ansi())
        else:
            time.sleep(10)
