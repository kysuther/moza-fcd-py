"""BlueBird 757 CDU app: display messages, key mapping, press timing, lights and the server (no sim needed)."""

import asyncio
import json
import os
import socket
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "apps", "bb757_cdu"))

import bb757_cdu as B  # noqa: E402
from moza_fcd import keys as K  # noqa: E402

# The 69 captain CDU key L-vars found in the 757 (L:B757_CDU_1_<name>)
AIRCRAFT_KEYS = set(
    "ATC DEPARR EXEC FIX FMCCOMM HOLD INIT KEYCLR KEYDEL KEYSLASH KEYSP LEGS MENU NAVRAD NEXT "
    "NUMPER NUMPLUS PREV PROG RTE VNAV".split()
) | {f"KEY{c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"} | {f"NUM{d}" for d in "0123456789"} \
  | {f"{s}{n}" for s in "LR" for n in range(1, 7)}


def display(rows):
    """A Display message like the 757 sends: 14 rows of text, all green, row 0 large and the rest small."""
    cells = []
    for r in range(14):
        text = (rows[r] if r < len(rows) else "").ljust(24)[:24]
        cells += [[ch, "g", 0 if r == 0 else 1] if ch != " " else [] for ch in text]
    return {"Target": "Display", "Data": cells}


class FakeSim:
    def __init__(self):
        self.writes, self.ready, self.lights = [], True, {}

    def write(self, lvar, value, not_before=0.0):
        self.writes.append((lvar, value, round(not_before, 3)))


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


def make(clock=None):
    screen, server, sim = FakeScreen(), B.ScreenServer(), FakeSim()
    cdu = B.Bb757Cdu(screen, server, sim, clock=clock or (lambda: 0.0))
    return screen, server, sim, cdu


class KeyMapTests(unittest.TestCase):
    def test_every_aircraft_key_is_reachable(self):
        self.assertEqual(set(B.KEY_MAP.values()), AIRCRAFT_KEYS)

    def test_every_mapped_key_is_on_the_fcd(self):
        fcd = set(K.ALL_KEYS)
        self.assertEqual(set(B.KEY_MAP) - fcd, set())
        self.assertEqual(fcd - set(B.KEY_MAP), {"BRT-", "BRT+"})

    def test_names(self):
        self.assertEqual(B.lvar_for("A"), "B757_CDU_1_KEYA")
        self.assertEqual(B.lvar_for("7"), "B757_CDU_1_NUM7")
        self.assertEqual(B.lvar_for("LSK3R"), "B757_CDU_1_R3")
        self.assertEqual(B.lvar_for("CLB"), "B757_CDU_1_VNAV")
        self.assertEqual(B.lvar_for("N1_LIMIT"), "B757_CDU_1_NAVRAD")
        self.assertEqual(B.lvar_for("CLR"), "B757_CDU_1_KEYCLR")
        self.assertIsNone(B.lvar_for("BRT+"))


class KeyTests(unittest.TestCase):
    def test_press_and_release_hold_the_minimum_time(self):
        now = [10.0]
        screen, _, sim, _ = make(clock=lambda: now[0])
        screen.on_key("A", True)
        now[0] = 10.02
        screen.on_key("A", False)
        self.assertEqual(sim.writes, [("B757_CDU_1_KEYA", 1, 0.0), ("B757_CDU_1_KEYA", 0, 10.1)])

    def test_release_without_press(self):
        screen, _, sim, _ = make()
        screen.on_key("EXEC", False)
        self.assertEqual(sim.writes, [("B757_CDU_1_EXEC", 0, 0.0)])

    def test_brightness_keys_drive_the_fcd(self):
        screen, _, sim, cdu = make()
        cdu.brightness = 95
        screen.on_key("BRT+", True)
        screen.on_key("BRT+", False)
        self.assertEqual(screen.brightness, 100)
        screen.on_key("BRT-", True)
        self.assertEqual(screen.brightness, 90)
        self.assertEqual(sim.writes, [])

    def test_unmapped_keys_are_ignored(self):
        screen, _, sim, _ = make()
        screen.on_key("BTN90", True)
        self.assertEqual(sim.writes, [])


class ScreenTests(unittest.TestCase):
    def test_render(self):
        pg = B.render(display(["     IDENT       1/2", "  MODEL", "757-200", "", "", "", "", "", "", "", "", "",
                               "<INDEX        POS INIT>", "SCRATCH"]))
        self.assertEqual(pg.row_text(0), "     IDENT       1/2".ljust(24))
        self.assertEqual(pg.row_text(12), "<INDEX        POS INIT> ")
        self.assertFalse(pg[0, 5].small)
        self.assertTrue(pg[1, 2].small)
        self.assertEqual(pg[2, 0].color, 4)          # green

    def test_control_chars_and_box(self):
        msg = display([])
        msg["Data"][0] = ["\x00", "g", 0]
        msg["Data"][1] = ["☐", "g", 0]
        pg = B.render(msg)
        self.assertEqual(pg[0, 0].char, " ")
        self.assertEqual(pg[0, 1].char, "□")

    def test_messages(self):
        server = B.ScreenServer()
        server.on_message(json.dumps({"Target": "Font", "Data": "Boeing"}))
        server.on_message("not json")
        self.assertIsNone(server.page)
        server.on_message(json.dumps(display(["HELLO"])))
        self.assertEqual(server.page.row_text(0).strip(), "HELLO")

    def test_waiting_states(self):
        screen, server, _, cdu = make()
        cdu.tick()
        self.assertIn("WAITING FOR THE 757", screen.page.to_text())
        server.clients = 1
        cdu.tick()
        self.assertIn("WAITING FOR CDU DATA", screen.page.to_text())
        server.on_message(json.dumps(display(["HELLO"])))
        cdu.tick()
        self.assertEqual(screen.page.row_text(0).strip(), "HELLO")
        server.error = "PORT 8320 IN USE"
        cdu.tick()
        self.assertIn("PORT 8320 IN USE", screen.page.to_text())


class LightTests(unittest.TestCase):
    def test_lights_from(self):
        lights = B.lights_from({"FMC_EXEC_ACTIVE_1": 1, "B757_CDU1_DSPY_decal": 1})
        self.assertEqual(lights, {"EXEC": True, "MSG": False, "FAIL": False, "OFST": False, "CALL": True})
        test = B.lights_from({"OVHD_TEST_LTS": 1})
        self.assertEqual(test, {"EXEC": False, "MSG": True, "FAIL": True, "OFST": True, "CALL": True})

    def test_tick_sends_changes_only_while_connected(self):
        screen, server, sim, cdu = make()
        sim.lights = {"EXEC": True}
        cdu.tick()
        self.assertFalse(screen.lights["EXEC"])      # 757 not connected yet
        server.clients = 1
        cdu.tick()
        self.assertTrue(screen.lights["EXEC"])
        screen.lights.clear()
        cdu.tick()
        self.assertEqual(screen.lights, {})           # nothing changed
        cdu.lights_off()
        self.assertEqual(set(screen.lights.values()), {False})


class SimLinkTests(unittest.TestCase):
    def test_writes_then_reads_lights(self):
        written = []
        values = {"FMC_EXEC_ACTIVE_1": 1.0}
        link = B.SimLink(connect=lambda: (lambda v: values.get(v, 0.0), lambda v, x: written.append((v, x))))
        link.write("B757_CDU_1_KEYA", 1)
        link.write("B757_CDU_1_KEYA", 0, time.monotonic() + 0.05)
        link.start()
        try:
            deadline = time.monotonic() + 3
            while not link.lights.get("EXEC") and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            link.stop()
        self.assertEqual(written, [("B757_CDU_1_KEYA", 1), ("B757_CDU_1_KEYA", 0)])
        self.assertTrue(link.lights["EXEC"])


def free_port():
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


@unittest.skipUnless(__import__("importlib").util.find_spec("websockets"), "websockets not installed")
class ServerTests(unittest.TestCase):
    def test_captain_path_feeds_the_page(self):
        import websockets
        server = B.ScreenServer(port=free_port()).start()
        try:
            async def client():
                for _ in range(50):
                    try:
                        async with websockets.connect(f"ws://localhost:{server.port}/winwing/cdu-co-pilot") as ws:
                            try:
                                await ws.send(json.dumps(display(["WRONG SEAT"])))
                                await ws.recv()                       # the server hangs up on other seats
                            except websockets.ConnectionClosed:
                                pass
                        break
                    except OSError:
                        await asyncio.sleep(0.05)
                async with websockets.connect(f"ws://localhost:{server.port}{B.PATH}") as ws:
                    await ws.send(json.dumps({"Target": "Font", "Data": "Boeing"}))
                    await ws.send(json.dumps(display(["HELLO"])))
                    for _ in range(50):
                        if server.page is not None:
                            break
                        await asyncio.sleep(0.02)
                    self.assertEqual(server.clients, 1)
            asyncio.run(client())
            self.assertEqual(server.page.row_text(0).strip(), "HELLO")
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
