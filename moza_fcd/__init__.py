"""Standalone Python driver for the MOZA FCD (MA3F / MB7F) display.

    from moza_fcd import FcdScreen, Page

    with FcdScreen() as screen:              # FcdScreen(port="sim") to run without hardware
        screen.wait_ready()
        screen.show(Page().center(6, "HELLO WORLD", "green"))
"""

from .device import FcdScreen, PortInfo, find_fcd_ports, list_moza_ports
from .page import COLOR_CODES, COLUMNS, ROWS, Cell, Page, remap_color
from .session import ScreenSession, SessionState

__all__ = [
    "FcdScreen", "PortInfo", "find_fcd_ports", "list_moza_ports",
    "Page", "Cell", "ROWS", "COLUMNS", "COLOR_CODES", "remap_color",
    "ScreenSession", "SessionState",
]

__version__ = "0.1.0"
