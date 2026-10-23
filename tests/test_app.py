"""Headless tests for the CDU app framework and demo app."""

import time
import unittest

from moza_fcd import keys as K
from moza_fcd.app import CLR_HOLD_S, CduApp
from moza_fcd.demo_app import build


class FakeScreen:
    def __init__(self):
        self.page = None
        self.lights = {}
        self.on_key = None

    def show(self, page):
        self.page = page

    def set_light(self, name, on):
        self.lights[name] = on
        return True

    def press(self, *names):
        for n in names:
            self.on_key(n, True)
            self.on_key(n, False)

    def row(self, r):
        return self.page.row_text(r)


class KeysTests(unittest.TestCase):
    def test_parse_and_typed(self):
        self.assertEqual(K.parse_lsk("LSK3R"), ("R", 3))
        self.assertIsNone(K.parse_lsk("EXEC"))
        self.assertEqual(K.typed_char("SP"), " ")
        self.assertIsNone(K.typed_char("EXEC"))
        self.assertEqual(len(set(K.ALL_KEYS)), len(K.ALL_KEYS))
        self.assertTrue(set(K.KEYBOARD_MAP.values()) <= set(K.ALL_KEYS))


class DemoAppTests(unittest.TestCase):
    def setUp(self):
        self.s = FakeScreen()
        self.app = build(self.s)
        self.app.go("index")

    def test_index_matches_reference_layout(self):
        self.assertIn("INIT/REF INDEX", self.s.row(0))
        self.assertTrue(self.s.row(2).startswith("<IDENT"))
        self.assertTrue(self.s.row(12).startswith("<OFFSET"))

    def test_scratchpad_typing_and_clr(self):
        self.s.press("A", "B", "SP", "1", "+/-")
        self.assertEqual(self.s.row(13).rstrip(), "AB 1-")
        self.s.press("+/-")
        self.assertEqual(self.s.row(13).rstrip(), "AB 1+")
        self.s.press("CLR")
        self.assertEqual(self.s.row(13).rstrip(), "AB 1")

    def test_clr_hold_clears_all(self):
        self.s.press("A", "B", "C")
        self.s.on_key("CLR", True)
        self.app._clr_down_at -= CLR_HOLD_S + 0.1  # pretend it was held
        self.s.on_key("CLR", False)
        self.assertEqual(self.s.row(13).strip(), "")

    def test_perf_mod_exec_flow(self):
        self.s.press("LSK3L")
        self.assertIn("PERF INIT", self.s.row(0))
        self.s.press("1", "4", "2", ".", "5", "LSK1L")
        self.assertIn("MOD PERF INIT", self.s.row(0))
        self.assertTrue(self.s.lights["EXEC"])
        self.assertTrue(self.s.row(2).startswith("142.5"))
        self.s.press("EXEC")
        self.assertFalse(self.s.lights["EXEC"])
        self.assertNotIn("MOD", self.s.row(0))

    def test_invalid_entry_lights_msg(self):
        self.s.press("LSK3L", "9", "9", "9", "9", "LSK1L")
        self.assertEqual(self.s.row(13).strip(), "INVALID ENTRY")
        self.assertTrue(self.s.lights["MSG"])
        self.s.press("CLR")
        self.assertFalse(self.s.lights["MSG"])

    def test_erase_cancels_mod(self):
        self.s.press("LSK3L", "3", "5", "0", "LSK3L")
        self.assertTrue(self.app.exec_pending)
        self.s.press("LSK6L")  # <ERASE
        self.assertFalse(self.app.exec_pending)
        self.assertTrue(self.s.row(6).startswith("____"))

    def test_notes_and_paging(self):
        self.s.press("LSK1R", "H", "I", "LSK2L")
        self.assertTrue(self.s.row(4).startswith("HI"))
        self.s.press("NEXT_PAGE")
        self.assertIn("2/2", self.s.row(0))
        self.assertIn("NOTE 6", self.s.row(1))
        self.s.press("PREV_PAGE", "DEL", "LSK2L")
        self.assertTrue(self.s.row(4).startswith("-" * 10))

    def test_function_keys(self):
        self.s.press("MENU")
        self.assertIn("MENU", self.s.row(0))
        self.s.press("INIT_REF")
        self.assertIn("INIT/REF INDEX", self.s.row(0))


if __name__ == "__main__":
    unittest.main()
