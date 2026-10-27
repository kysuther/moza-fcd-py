"""PMDG 737 CDU app: screen decoding, key events, press timing and lights (no sim needed)."""

import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "apps", "pmdg737_cdu"))

import pmdg737_cdu as P  # noqa: E402
from moza_fcd import keys as K  # noqa: E402


def screen(rows, color=0, flags=0, powered=True, cells=None):
    """PMDG_NG3_CDU_Screen bytes: Cells[column][row] of (symbol, colour, flags), then Powered."""
    raw = bytearray(P.SCREEN_BYTES)
    for r, text in enumerate(rows):
        for c, ch in enumerate(text[:24]):
            i = (c * 14 + r) * 3
            raw[i:i + 3] = bytes([ord(ch), color, flags])
    for (r, c), cell in (cells or {}).items():
        i = (c * 14 + r) * 3
        raw[i:i + 3] = bytes(cell)
    raw[-1] = 1 if powered else 0
    return bytes(raw)


class FakeSim:
    def __init__(self, cdu=0):
        self.cdu, self.sent, self.connected, self.screen_raw, self.lights = cdu, [], True, None, {}

    def send(self, event, data, not_before=0.0):
        self.sent.append((event, data, round(not_before, 3)))


class FakeScreen:
    def __init__(self):
        self.page, self.on_key, self.lights, self.brightness = None, None, {}, None

    def show(self, page):
        self.page = page

    def set_light(self, name, on):
        self.lights[name] = on
        return True

    def set_screen_brightness(self, value):
        self.brightness = value


class KeyEventTests(unittest.TestCase):
    def test_every_fcd_key_but_brightness_has_an_event(self):
        self.assertEqual({k for k in K.ALL_KEYS if P.event_for(k) is None}, {"BRT-", "BRT+"})

    def test_events_match_the_sdk(self):
        self.assertEqual(P.event_for("LSK1L"), 69632 + 534)       # EVT_CDU_L_L1
        self.assertEqual(P.event_for("LSK6R"), 69632 + 545)       # EVT_CDU_L_R6
        self.assertEqual(P.event_for("1"), 69632 + 561)
        self.assertEqual(P.event_for("9"), 69632 + 569)
        self.assertEqual(P.event_for("0"), 69632 + 571)
        self.assertEqual(P.event_for("Z"), 69632 + 598)
        self.assertEqual(P.event_for("CLR"), 69632 + 602)
        self.assertEqual(P.event_for("N1_LIMIT"), 69632 + 557)
        self.assertEqual(P.event_for("CLR", cdu=1), 69632 + 674)  # EVT_CDU_R_CLR

    def test_events_are_unique(self):
        events = [P.event_for(k) for k in P.KEY_EVENTS]
        self.assertEqual(len(events), len(set(events)))


class KeyTests(unittest.TestCase):
    def make(self, cdu=0):
        now = [5.0]
        sim, scr = FakeSim(cdu), FakeScreen()
        cdu_app = P.Pmdg737Cdu(scr, sim, clock=lambda: now[0])
        return now, sim, scr, cdu_app

    def test_click_and_release(self):
        now, sim, scr, _ = self.make()
        scr.on_key("EXEC", True)
        now[0] = 5.03
        scr.on_key("EXEC", False)
        ev = 69632 + 556
        self.assertEqual(sim.sent, [(ev, P.MOUSE_LEFTSINGLE, 0.0), (ev, P.MOUSE_LEFTRELEASE, 5.1)])

    def test_first_officer(self):
        _, sim, scr, _ = self.make(cdu=1)
        scr.on_key("A", True)
        self.assertEqual(sim.sent[0][0], 69632 + 573 + 72)

    def test_brightness(self):
        _, sim, scr, app = self.make()
        app.brightness = 5
        scr.on_key("BRT-", True)
        self.assertEqual(scr.brightness, 0)
        self.assertEqual(sim.sent, [])


class ScreenTests(unittest.TestCase):
    def test_render(self):
        pg = P.render(screen(["      PERF INIT     1/2"], color=2, flags=P.FLAG_SMALL))
        self.assertEqual(pg.row_text(0), "      PERF INIT     1/2 ")
        self.assertTrue(pg[0, 6].small)
        self.assertEqual(pg[0, 6].color, 4)                       # green

    def test_symbols_colours_and_flags(self):
        pg = P.render(screen([], cells={(2, 0): (0xEA, 4, 0), (2, 1): (0xB0, 0, 0), (2, 2): (0xA1, 1, 0),
                                        (2, 3): (ord("X"), 5, P.FLAG_UNUSED), (2, 4): (0x00, 0, 0),
                                        (2, 5): (0x07, 0, 0)}))
        self.assertEqual(pg.row_text(2)[:6], "□°←X  ")
        self.assertEqual(pg[2, 0].color, 6)                       # amber
        self.assertEqual(pg[2, 2].color, 1)                       # cyan
        self.assertEqual(pg[2, 3].color, 8)                       # unused -> grey

    def test_unpowered(self):
        self.assertIsNone(P.render(screen(["HELLO"], powered=False)))
        self.assertIsNone(P.render(b"short"))

    def test_frames(self):
        sim, scr = FakeSim(), FakeScreen()
        app = P.Pmdg737Cdu(scr, sim)
        sim.connected = False
        self.assertIn("WAITING FOR THE SIM", app.frame().to_text())
        sim.connected = True
        self.assertIn("WAITING FOR PMDG 737", app.frame().to_text())
        sim.screen_raw = screen(["HELLO"])
        self.assertEqual(app.frame().row_text(0).strip(), "HELLO")
        sim.screen_raw = screen(["HELLO"], powered=False)
        self.assertEqual(app.frame().to_text().strip(), "")


class LightTests(unittest.TestCase):
    def test_lights_from(self):
        raw = bytes([1, 0, 0, 1, 0, 0, 1, 1, 0, 0])               # EXEC[2] CALL[2] FAIL[2] MSG[2] OFST[2]
        self.assertEqual(P.lights_from(raw, 0), {"EXEC": True, "CALL": False, "FAIL": False, "MSG": True,
                                                  "OFST": False})
        self.assertEqual(P.lights_from(raw, 1), {"EXEC": False, "CALL": True, "FAIL": False, "MSG": True,
                                                  "OFST": False})

    def test_tick(self):
        sim, scr = FakeSim(), FakeScreen()
        app = P.Pmdg737Cdu(scr, sim)
        sim.lights = {"EXEC": True}
        app.tick()
        self.assertTrue(scr.lights["EXEC"])
        scr.lights.clear()
        app.tick()
        self.assertEqual(scr.lights, {})
        sim.connected = False
        app.tick()
        self.assertFalse(scr.lights["EXEC"])


class FakeSimConnect:
    def __init__(self, messages):
        self.messages, self.subscribed, self.sent = list(messages), [], []

    def subscribe(self, *args):
        self.subscribed.append(args)

    def next(self):
        return self.messages.pop(0) if self.messages else None

    def transmit(self, event, data):
        self.sent.append((event, data))

    def close(self):
        pass


class SimLinkTests(unittest.TestCase):
    def test_routes_messages_and_sends_keys(self):
        raw = screen(["HELLO"])
        fake = FakeSimConnect([(16, P.SimLink.SCREEN, raw + b"\0\0\0"), (16, P.SimLink.LIGHTS, bytes([1] + [0] * 9))])
        link = P.SimLink(connect=lambda: fake)
        link.send(70000, P.MOUSE_LEFTSINGLE)
        link.start()
        try:
            deadline = time.monotonic() + 3
            while (link.screen_raw is None or not fake.sent) and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            link.stop()
        self.assertEqual(link.screen_raw, raw)
        self.assertTrue(link.lights["EXEC"])
        self.assertEqual(fake.sent, [(70000, P.MOUSE_LEFTSINGLE)])
        self.assertEqual([s[0] for s in fake.subscribed], [b"PMDG_NG3_CDU_0", b"PMDG_NG3_Data"])
        self.assertEqual(fake.subscribed[1][3:5], (543, 10))


if __name__ == "__main__":
    unittest.main()
