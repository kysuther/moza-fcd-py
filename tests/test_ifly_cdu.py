"""iFly 737 MAX CDU app: key mapping, screen rendering, lights and key forwarding (no EFB or sim needed)."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "apps", "ifly_cdu"))

import ifly_cdu as I  # noqa: E402
from moza_fcd import keys as K  # noqa: E402


def grid(fill):
    return [[fill for _ in range(24)] for _ in range(14)]


def gau(text_rows=None, colors=None, small=None, **lights):
    chars = grid(" ")
    for r, text in (text_rows or {}).items():
        for c, ch in enumerate(text):
            chars[r][c] = ch
    data = {"LSKChar": [chars, grid(" ")], "LSK_Color": [colors or grid(0), grid(0)],
            "LSK_SmallFont": [small or grid(False), grid(False)], "CDU_Brightness_Status": [20, 20]}
    for name in I.LIGHTS:
        data[f"CDU_{name}_Status"] = [int(lights.get(name, 0)), 0]
    return data


class FakeLink:
    def __init__(self):
        self.sent, self.gau, self.connected, self.sdk_connected, self.rejected = [], None, True, True, ""

    def send_key(self, key_id, v1=1, v2=0):
        self.sent.append((key_id, v1, v2))


class FakeScreen:
    def __init__(self):
        self.page, self.lights, self.on_key, self.brightness = None, {}, None, []

    def show(self, page):
        self.page = page

    def set_light(self, name, on):
        self.lights[name] = on
        return True

    def set_screen_brightness(self, v):
        self.brightness.append(v)
        return True


class KeyMapTests(unittest.TestCase):
    def test_every_fcd_key_maps_except_brightness(self):
        unmapped = {k for k in K.ALL_KEYS if I.ifly_key(k) is None}
        self.assertEqual(unmapped, {"BRT-", "BRT+"})

    def test_renamed_and_lsk_keys(self):
        self.assertEqual([I.ifly_key(k) for k in ("CLB", "CRZ", "DES", "LSK3L", "LSK6R", ".", "/", "+/-", "SP", "Q")],
                         ["VNAV", "FMC_COMM", "ATC", "LSK_L3", "LSK_R6", "PERIOD", "SLASH", "ADDMINUS", "SP", "Q"])


class RenderTests(unittest.TestCase):
    def test_text_colours_sizes_and_symbols(self):
        colors = grid(0)
        small = grid(False)
        colors[0][0], colors[0][1], colors[0][2] = 1, 2, 5          # green, cyan, box
        colors[1][3], colors[1][4], colors[1][5] = 6, 9, 13         # degree, left arrow, block
        small[2][0] = True
        pg = I.render(gau({0: "ABC", 2: "x"}, colors, small))
        self.assertEqual(pg.row_text(0)[:3], "AB□")
        self.assertEqual(pg.row_text(1)[3:6], "°←■")
        self.assertEqual((pg[0, 0].color, pg[0, 1].color), (4, 1))  # FCD codes: green 4, cyan 1
        self.assertTrue(pg[2, 0].small)
        self.assertFalse(pg[0, 0].small)

    def test_numeric_chars_and_short_rows(self):
        data = gau({0: "OK"})
        data["LSKChar"][0][1][0] = 65                                # 'A' as a number
        data["LSKChar"][0][2] = ["Z"]                                # short row
        pg = I.render(data)
        self.assertEqual((pg.row_text(1)[0], pg.row_text(2)[0]), ("A", "Z"))

    def test_nul_and_control_characters_become_blanks(self):
        data = gau({13: "KORD"})
        data["LSKChar"][0][13][4] = "\x00"                          # what the iFly actually sends
        data["LSKChar"][0][13][5] = 7
        pg = I.render(data)
        self.assertEqual(pg.row_text(13), "KORD" + " " * 20)
        self.assertNotIn("\x00", pg.to_text())

    def test_lights(self):
        self.assertEqual(I.lights_from(gau(EXEC=1, MSG=1)),
                         {"EXEC": True, "MSG": True, "FAIL": False, "CALL": False, "OFST": False})


class AppTests(unittest.TestCase):
    def setUp(self):
        self.screen, self.link = FakeScreen(), FakeLink()
        self.app = I.IflyCdu(self.screen, self.link)

    def press(self, *names):
        for n in names:
            self.screen.on_key(n, True)
            self.screen.on_key(n, False)

    def test_keys_forwarded_clr_down_up_and_brt_local(self):
        self.press("LSK1L", "CLB", "A")
        self.screen.on_key("CLR", True)
        self.screen.on_key("CLR", False)
        self.app.brightness = 70
        self.press("BRT-", "BRT+", "BRT+")
        self.assertEqual(self.link.sent, [("LSK_L1", 1, 0), ("VNAV", 1, 0), ("A", 1, 0), ("CLR", 0, 0), ("CLR", 0, 1)])
        self.assertEqual(self.screen.brightness, [60, 70, 80])

    def test_waiting_screens_then_mirror_with_lights(self):
        self.link.connected = False
        self.app.tick()
        self.assertIn("WAITING FOR IFLY EFB", self.screen.page.to_text())
        self.link.connected = True
        self.app.tick()
        self.assertIn("WAITING FOR AIRCRAFT", self.screen.page.to_text())
        self.link.gau = gau({0: "      IDENT         1/2 "}, EXEC=1)
        self.app.tick()
        self.assertEqual(self.screen.page.row_text(0), "      IDENT         1/2 ")
        self.assertTrue(self.screen.lights["EXEC"])
        self.screen.lights.clear()
        self.app.tick()
        self.assertEqual(self.screen.lights, {})                    # lights only sent on change
        self.link.connected = False
        self.app.tick()
        self.assertFalse(self.screen.lights["EXEC"])                # lights off while disconnected

    def test_link_keeps_only_cdu_fields(self):
        link = I.EfbLink()
        link.on_update({"sdk_connected": True, "plane": {}, "gau": dict(gau({0: "HI"}), Other_Field=1)})
        self.assertTrue(link.sdk_connected)
        self.assertNotIn("Other_Field", link.gau)
        self.assertEqual(I.render(link.gau).row_text(0)[:2], "HI")
        link.on_update({"plane": {}})                                # updates without gau keep the screen
        self.assertIsNotNone(link.gau)


if __name__ == "__main__":
    unittest.main()
