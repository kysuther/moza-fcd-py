"""Fenix A320 MCDU app: display XML parsing, key mapping and press timing (no sim or gateway needed)."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "apps", "fenix_mcdu"))

import fenix_mcdu as F  # noqa: E402
from moza_fcd import keys as K  # noqa: E402

# Captured live from the Fenix (F-PLN page, empty flight plan)
LIVE = """<root>
  <title> sFROMl          s      l £¢</title>
  <line>s        TIME  SPD/ALT   </line>
  <line>gPPOS   w ----  gwl---/ -----lgw</line>
  <line>s                   s     l</line>
  <line>---F-PLN DISCONTINUITY--</line>
  <line>s                   s     l</line>
  <line>------END OF F-PLN------</line>
  <line>s                   s     l</line>
  <line>-----NO ALTN F-PLN------</line>
  <line>s                        </line>
  <line>                        </line>
  <line>s DEST   TIME  DIST  EFOB</line>
  <line>------- l----l  sl----s l-----sl</line>
  <scratchpad>GPS PRIMARY             </scratchpad>
</root>"""


class FakeKeys:
    def __init__(self):
        self.writes = []

    def write(self, lvar, value, not_before=0.0):
        self.writes.append((lvar, value, round(not_before, 3)))


class FakeDisplay:
    def __init__(self, rows=None, connected=True):
        self.rows, self.connected = rows, connected


class FakeScreen:
    def __init__(self):
        self.page, self.on_key = None, None

    def show(self, page):
        self.page = page


class ParseTests(unittest.TestCase):
    def test_live_page(self):
        rows = F.parse_display(LIVE)
        self.assertEqual(len(rows), 14)
        pg = F.render(rows)
        self.assertEqual(pg.row_text(0), " FROM                 ←→")
        self.assertEqual(pg.row_text(2), "PPOS    ----  ---/ -----")    # exactly 24 wide
        self.assertEqual(pg.row_text(4), "---F-PLN DISCONTINUITY--")
        self.assertEqual(pg.row_text(13).rstrip(), "GPS PRIMARY")
        self.assertEqual(pg[2, 0].color, 4)                 # PPOS green
        self.assertTrue(pg[0, 1].small)                      # "FROM" small
        self.assertFalse(pg[0, 22].small)                    # arrows large

    def test_format_letters_and_symbols(self):
        cells = F.parse_row("aAB scyC#&")
        self.assertEqual(cells, [("A", "amber", False), ("B", "amber", False), (" ", "amber", False),
                                 ("C", "yellow", True), ("□", "yellow", True), ("Δ", "yellow", True)])
        self.assertEqual(len(F.parse_row("X" * 40)), 24)
        self.assertEqual(F.parse_display("<root><title/></root>")[0], [])


class KeyTests(unittest.TestCase):
    def test_every_fcd_key_is_mapped_except_exec(self):
        self.assertEqual({k for k in K.ALL_KEYS if F.lvar_for(k) is None}, {"EXEC"})

    def test_mapping(self):
        pairs = {"INIT_REF": "INIT", "RTE": "FPLN", "LEGS": "DIR", "DEP_ARR": "AIRPORT", "BRT+": "ARROW_UP",
                 "BRT-": "ARROW_DOWN", "NEXT_PAGE": "ARROW_RIGHT", "DEL": "OVFLY", "CLR": "CLEAR", "+/-": "MINUS",
                 "LSK3R": "LSK3R", "Q": "Q"}
        for key, name in pairs.items():
            self.assertEqual(F.lvar_for(key, 1), f"S_CDU1_KEY_{name}")
        self.assertEqual(F.lvar_for("A", 2), "S_CDU2_KEY_A")

    def test_press_release_with_minimum_hold(self):
        t = [100.0]
        keys, screen = FakeKeys(), FakeScreen()
        F.FenixMcdu(screen, FakeDisplay(), keys, side=1, clock=lambda: t[0])
        screen.on_key("A", True)
        t[0] += 0.01
        screen.on_key("A", False)
        screen.on_key("EXEC", True)
        self.assertEqual(keys.writes, [("S_CDU1_KEY_A", 1, 0.0), ("S_CDU1_KEY_A", 0, 100.1)])


class AppTests(unittest.TestCase):
    def test_waiting_then_mirror(self):
        screen, display = FakeScreen(), FakeDisplay(connected=False)
        app = F.FenixMcdu(screen, display, FakeKeys())
        app.tick()
        self.assertIn("WAITING FOR FENIX", screen.page.to_text())
        display.connected = True
        app.tick()
        self.assertIn("WAITING FOR MCDU DATA", screen.page.to_text())
        display.rows = F.parse_display(LIVE)
        app.tick()
        self.assertIn("F-PLN DISCONTINUITY", screen.page.to_text())

    def test_display_messages(self):
        d = F.DisplayLink()
        d.on_message({"type": "next", "payload": {"data": {"dataRefs": {"name": "aircraft.mcdu1.display",
                                                                         "value": LIVE}}}})
        self.assertEqual(F.render(d.rows).row_text(4), "---F-PLN DISCONTINUITY--")
        sent = []

        class WS:
            def send(self, s):
                sent.append(s)
        d.on_message({"type": "ping"}, WS())
        self.assertEqual(sent, ['{"type": "pong"}'])
        d.on_message({"type": "next", "payload": {"data": {"dataRefs": {"value": "<broken"}}}})
        self.assertIsNotNone(d.rows)                         # a bad update keeps the last screen


if __name__ == "__main__":
    unittest.main()
