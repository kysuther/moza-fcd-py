"""Key map, main-board commands, HID diffing, and the Windows bindings' struct layouts."""

import sys
import time
import unittest

from moza_fcd import FcdScreen
from moza_fcd import keys as K
from moza_fcd import protocol as p
from moza_fcd.buttons import button_events
from moza_fcd.session import ScreenSession, SettingsChannel


class KeyMapTests(unittest.TestCase):
    def test_mb7f_map_covers_every_key_once(self):
        self.assertEqual(sorted(K.MB7F_HID_BUTTONS), list(range(1, 72)))  # buttons 1..71
        self.assertEqual(sorted(K.MB7F_HID_BUTTONS.values()), sorted(K.ALL_KEYS))

    def test_spot_checks_from_cockpit_layout(self):
        m = K.MB7F_HID_BUTTONS
        self.assertEqual((m[43], m[48], m[49], m[54]), ("LSK1L", "LSK6L", "LSK1R", "LSK6R"))
        self.assertEqual((m[57], m[62], m[71]), ("INIT_REF", "MENU", "EXEC"))
        self.assertEqual((m[13], m[37], m[38], m[42]), ("A", "Y", "Z", "CLR"))
        self.assertEqual((m[1], m[10], m[11], m[12]), ("1", ".", "0", "+/-"))
        self.assertEqual((m[56], m[55]), ("BRT-", "BRT+"))

    def test_lights(self):
        self.assertEqual(set(K.LED_IDS), set(K.LIGHTS))
        self.assertIn("CALL", K.LIGHTS)


class ButtonEventTests(unittest.TestCase):
    def test_diff_releases_first_and_unknown_buttons(self):
        old, new = frozenset({43, 71}), frozenset({71, 13, 99})
        self.assertEqual(button_events(old, new, K.MB7F_HID_BUTTONS),
                         [("LSK1L", False), ("A", True), ("BTN99", True)])
        self.assertEqual(button_events(new, new, K.MB7F_HID_BUTTONS), [])


class DeviceCommandTests(unittest.TestCase):
    def test_led_enable_frame(self):
        # group 69 (0x45) in the Command byte, id 98 (0x62) + args in the payload
        self.assertEqual(p.encode_device_command(p.CMD_LED_ENABLE, (3, 1)), bytes.fromhex("7e 03 45 12 62 03 01 4b"))
        self.assertEqual(p.encode_device_command(p.CMD_SET_BRIGHTNESS + (2,), (128,)),
                         p.encode_serial_frame(0x45, 0x12, bytes([83, 2, 128])))

    def test_brightness_controls_against_simulator(self):
        old = SettingsChannel.COLLECTION_WINDOW_S
        SettingsChannel.COLLECTION_WINDOW_S = 0.3
        try:
            with FcdScreen(port="sim") as screen:
                self.assertTrue(screen.wait_ready(5))
                self.assertEqual((screen.screen_brightness(), screen.key_backlight(), screen.indicator_brightness()),
                                 (73, 5, 5))
                screen.set_screen_brightness(40)
                screen.set_key_backlight(20)          # clamped to 15
                screen.set_indicator_brightness(3)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and screen.screen_brightness() != 40:
                    time.sleep(0.02)
                self.assertEqual((screen.screen_brightness(), screen.key_backlight(), screen.indicator_brightness()),
                                 (40, 15, 3))
                sim = screen.simulator
                self.assertEqual(sim.screen_brightness, 40)
                self.assertIn((0x45, 81, 1, 0), sim.device_commands)   # keys: manual mode before range
                self.assertIn((0x45, 82, 1, 15), sim.device_commands)
        finally:
            SettingsChannel.COLLECTION_WINDOW_S = old

    def test_light_sensor_readings_are_kept(self):
        screen = FcdScreen(port="sim")
        seen = []
        screen.on_light = lambda name, value: seen.append((name, value))
        screen.emit_axis("LIGHT_SENSOR_AB", 21673)
        self.assertEqual((screen.light_levels, seen), ({"LIGHT_SENSOR_AB": 21673}, [("LIGHT_SENSOR_AB", 21673)]))

    def test_device_read_ids(self):
        self.assertEqual(p.device_read_ids(p.CMD_GET_BRIGHTNESS_RANGE, 1), (68, 82, 1, 0))
        self.assertEqual(p.device_read_ids(p.CMD_GET_BRIGHTNESS_RANGE, 3), (68, 82, 3))
        self.assertEqual(p.device_read_ids(p.CMD_GET_INDICATOR_MODE, 1), (68, 81, 1))

    def test_not_sent_before_running(self):
        sent = []
        s = ScreenSession(sent.append)
        self.assertFalse(s.send_device_command(p.CMD_LED_ENABLE, (0, 1)))
        self.assertEqual(sent, [])

    def test_lights_reach_simulator_over_the_wire(self):
        old = SettingsChannel.COLLECTION_WINDOW_S
        SettingsChannel.COLLECTION_WINDOW_S = 0.3
        try:
            with FcdScreen(port="sim") as screen:
                self.assertTrue(screen.wait_ready(5))
                self.assertTrue(screen.set_light("EXEC", True))
                self.assertTrue(screen.set_light("CALL", True))
                self.assertFalse(screen.set_light("NOPE", True))
                self.assertTrue(screen.set_brightness(300, group=1))  # clamped to 255
                sim = screen.simulator
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not (sim.lights.get("CALL") and sim.brightness):
                    time.sleep(0.02)
                self.assertEqual(sim.lights, {"EXEC": True, "CALL": True})
                self.assertEqual(sim.brightness, {1: 255})
                self.assertIn((0x45, 98, K.LED_IDS["CALL"], 1), sim.device_commands)
                # the device's 0xC5 replies must not disturb the screen session
                self.assertEqual(screen.state.value, "running")
        finally:
            SettingsChannel.COLLECTION_WINDOW_S = old


@unittest.skipUnless(sys.platform == "win32", "Windows bindings")
class WindowsBindingTests(unittest.TestCase):
    def test_sdk_struct_layouts_match_packed_c_headers(self):
        import ctypes
        from moza_fcd import sdk
        self.assertEqual(ctypes.sizeof(sdk.MOZA_VALUE), 16)
        self.assertEqual(ctypes.sizeof(sdk.MOZA_DEVICE_INFO), 349 if ctypes.sizeof(ctypes.c_void_p) == 8 else 345)
        self.assertEqual(ctypes.sizeof(sdk.MOZA_COMMAND_INFO), 70 + ctypes.sizeof(ctypes.c_void_p))

    def test_hid_enumeration_runs(self):
        from moza_fcd import hidwin
        devices = hidwin.enumerate_devices()
        self.assertIsInstance(devices, list)


if __name__ == "__main__":
    unittest.main()
