"""Colour names, WinWing letters and remap_color (grey is black on some units)."""

import unittest

from moza_fcd import Page, remap_color
from moza_fcd import page as P


class RemapColorTests(unittest.TestCase):
    def tearDown(self):
        remap_color("grey", "grey")
        remap_color("cyan", "cyan")

    def test_grey_defaults_to_code_8(self):
        self.assertEqual(Page().write(0, 0, "X", "grey")[0, 0].color, 8)
        self.assertEqual(P.color_code("e"), 8)

    def test_remap_grey_to_white_and_back(self):
        remap_color("grey", "white")
        self.assertEqual(Page().write(0, 0, "X", "grey")[0, 0].color, 7)
        self.assertEqual(P.color_code("e"), 7)             # WinWing grey follows
        self.assertEqual(P.color_code("w"), 7)
        remap_color("grey", "grey")
        self.assertEqual(P.color_code("grey"), 8)

    def test_remap_follows_letter_aliases_and_rejects_unknown(self):
        remap_color("cyan", "green")
        self.assertEqual((P.color_code("c"), P.color_code("o")), (4, 4))
        with self.assertRaises(ValueError):
            remap_color("grey", "beige")
        with self.assertRaises(ValueError):
            remap_color("beige", "white")


class ControlCharacterTests(unittest.TestCase):
    def test_control_characters_are_written_as_blanks(self):
        pg = Page().write(0, 0, "KORD\x00\t\nX\x7f")
        self.assertEqual(pg.row_text(0)[:9], "KORD   X ")


if __name__ == "__main__":
    unittest.main()
