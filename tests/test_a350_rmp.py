"""A350 RMP app: entry parsing, page flows, backend calls, and the MSFS bridge (fake SimConnect)."""

import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "apps", "a350_rmp"))
sys.path.insert(0, HERE)

import a350_rmp as R  # noqa: E402
import rmp_msfs as M  # noqa: E402
from test_app import FakeScreen  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_com(self):
        self.assertEqual(R.parse_com("118.7"), "118.700")
        self.assertEqual(R.parse_com("1187"), "118.700")
        self.assertEqual(R.parse_com("118700"), "118.700")
        self.assertEqual(R.parse_com("18.7"), "118.700")       # leading 1 may be dropped
        self.assertEqual(R.parse_com("121.505"), "121.505")    # 8.33 channel
        self.assertEqual(R.parse_com("136.990"), "136.990")
        for bad in ("117.9", "137.0", "118.720", "136.995", "ABC", "1", "118.7055"):
            self.assertIsNone(R.parse_com(bad), bad)

    def test_nav(self):
        self.assertEqual(R.parse_nav("110.3", "LS"), "110.30")
        self.assertEqual(R.parse_nav("10935", "LS"), "109.35")
        self.assertIsNone(R.parse_nav("110.20", "LS"))         # ILS tenths must be odd
        self.assertIsNone(R.parse_nav("112.10", "LS"))
        self.assertEqual(R.parse_nav("114.7", "VOR"), "114.70")
        self.assertEqual(R.parse_nav("117.95", "VOR"), "117.95")
        self.assertIsNone(R.parse_nav("118.00", "VOR"))
        self.assertIsNone(R.parse_nav("114.73", "VOR"))

    def test_adf_crs_squawk(self):
        self.assertEqual(R.parse_adf("350"), "350.0")
        self.assertEqual(R.parse_adf("1750.5"), "1750.5")
        self.assertIsNone(R.parse_adf("150"))
        self.assertEqual(R.parse_crs("360"), 0)
        self.assertEqual(R.parse_crs("45"), 45)
        self.assertIsNone(R.parse_crs("361"))
        self.assertEqual(R.parse_squawk("7700"), "7700")
        self.assertIsNone(R.parse_squawk("7800"))
        self.assertIsNone(R.parse_squawk("123"))


class RecordingBackend(R.LocalBackend):
    def __init__(self):
        self.calls = []

    def __getattribute__(self, name):
        attr = object.__getattribute__(self, name)
        if name.startswith(("set_", "swap_", "ident")):
            def rec(*a):
                object.__getattribute__(self, "calls").append((name,) + a)
            return rec
        return attr


class PageTests(unittest.TestCase):
    def setUp(self):
        self.s = FakeScreen()
        self.be = RecordingBackend()
        self.rmp = R.Rmp(self.s, self.be)
        self.rmp.app.go("vhf")

    def row(self, r):
        return self.s.row(r)

    def test_vhf_layout(self):
        self.assertIn("VHF", self.row(0))
        self.assertTrue(self.row(2).startswith("118.700"))
        self.assertTrue(self.row(2).rstrip().endswith("121.800"))
        self.assertIn("VHF1 TX", self.row(1))

    def test_enter_standby_and_swap(self):
        self.s.press("1", "2", "4", ".", "3", "5", "LSK1R")
        self.assertTrue(self.row(2).rstrip().endswith("124.350"))
        self.s.press("LSK1L")
        self.assertTrue(self.row(2).startswith("124.350"))
        self.assertEqual(self.be.calls, [("set_com_standby", 1, "124.350"), ("swap_com", 1)])

    def test_invalid_frequency(self):
        self.s.press("1", "5", "0", "LSK2R")
        self.assertEqual(self.row(13).strip(), "INVALID ENTRY")
        self.assertTrue(self.s.lights["MSG"])
        self.assertEqual(self.be.calls, [])

    def test_empty_scratchpad_prompt(self):
        self.s.press("LSK3R")
        self.assertEqual(self.row(13).strip(), "ENTER DATA IN SCRATCHPAD")

    def test_transmit_cycle(self):
        self.s.press("LSK4L")
        self.assertIn("VHF2 TX", self.row(3))
        self.s.press("LSK4L", "LSK4L")
        self.assertIn("VHF1 TX", self.row(1))
        self.assertEqual(self.be.calls, [("set_tx", 2), ("set_tx", 3), ("set_tx", 1)])

    def test_shortcut_keys_and_cycle(self):
        self.s.press("DES")
        self.assertIn("NAV", self.row(0))
        self.s.press("CRZ")
        self.assertIn("ATC / TCAS", self.row(0))
        self.s.press("NEXT_PAGE")
        self.assertIn("VHF", self.row(0))
        self.s.press("PREV_PAGE")
        self.assertIn("ATC", self.row(0))
        self.s.press("RTE")
        self.assertEqual(self.row(13).strip(), "HF NOT AVAILABLE")
        self.s.press("CLR", "MENU")
        self.assertIn("RMP MENU", self.row(0))
        self.assertIn("STANDALONE", self.s.page.to_text())

    def test_nav_requires_stby_rad_nav(self):
        self.s.press("DES", "LSK2R")                 # VOR page
        self.s.press("1", "1", "3", ".", "9", "LSK1L")
        self.assertEqual(self.row(13).strip(), "SELECT STBY RAD NAV")
        self.s.press("CLR", "CLR", "CLR", "CLR", "CLR", "CLR", "LSK6L", "LSK2L")   # back to NAV, toggle mode
        self.assertIn("STBY RAD NAV", self.row(4))
        self.s.press("LSK2R", "1", "1", "3", ".", "9", "LSK1L", "2", "7", "0", "LSK1R")
        self.assertTrue(self.row(2).startswith("113.90"))
        self.assertTrue(self.row(2).rstrip().endswith("270"))
        self.assertEqual(self.be.calls, [("set_nav", "VOR", 1, "113.90"), ("set_crs", "VOR", 1, 270)])

    def test_ls_and_adf(self):
        self.rmp.state.nav_mode = "STBY"
        self.s.press("DES", "LSK1R", "1", "0", "9", ".", "5", "LSK1L", "1", "2", "LSK1R")
        self.assertTrue(self.row(2).startswith("109.50"))
        self.assertTrue(self.row(2).rstrip().endswith("012"))
        self.s.press("LSK6L", "LSK3R", "3", "9", "0", "LSK2L")
        self.assertTrue(self.row(4).startswith("390.0"))
        self.assertIn(("set_adf", 2, "390.0"), self.be.calls)

    def test_atc_squawk_ident_and_modes(self):
        self.s.press("CRZ", "7", "7", "0", "0", "LSK4L")
        self.assertTrue(self.row(8).startswith("7700"))
        self.s.press("LSK3L")
        self.assertIn("TRANSMITTING IDENT", self.row(6))
        self.s.press("LSK1R")                        # ATC MODE -> STBY
        self.assertIn("STBY>", self.row(2))
        self.assertIn("INOP", self.row(4))
        self.assertIn("TCAS NEEDS ATC MODE AUTO", self.s.page.to_text())
        self.s.press("LSK2L")                        # ALT RPTG off
        self.assertIn("<OFF", self.row(4))
        self.s.press("LSK1R")                        # back to AUTO
        self.assertEqual(self.rmp.state.xpdr_state, "ON")
        self.assertEqual(self.be.calls, [("set_squawk", "7700"), ("ident",), ("set_xpdr_state", "STBY"),
                                         ("set_xpdr_state", "STBY"), ("set_xpdr_state", "ON")])

    def test_atc_mode_read_only_when_backend_cannot_set_it(self):
        self.be.can = lambda what: what != "xpdr_state"
        self.s.press("CRZ", "LSK1R", "LSK2L")
        self.assertEqual(self.row(13).strip(), "SET ATC MODE IN COCKPIT")
        self.assertEqual((self.rmp.state.atc_mode, self.rmp.state.alt_rptg), ("AUTO", True))
        self.assertNotIn(("set_xpdr_state", "STBY"), self.be.calls)

    def test_vor_adf_read_only_when_backend_cannot_tune(self):
        self.be.can = lambda what: what not in ("vor_freq", "adf_freq")
        self.rmp.state.nav_mode = "STBY"
        self.s.press("DES", "LSK2R", "1", "1", "3", ".", "9", "LSK1L")
        self.assertEqual(self.row(13).strip(), "TUNE VOR IN COCKPIT")
        self.s.press("CLR", "LSK6L", "LSK3R", "LSK1L")
        self.assertEqual(self.row(13).strip(), "TUNE ADF IN COCKPIT")
        self.assertEqual(self.be.calls, [])

    def test_tcas_locked_while_transponder_stby(self):
        self.rmp.state.atc_mode = "STBY"
        self.s.press("CRZ", "LSK2R")
        self.assertEqual(self.row(13).strip(), "ATC MODE AUTO REQD")
        self.assertEqual(self.rmp.state.tcas, "TA/RA")
        self.assertEqual(self.be.calls, [])

    def test_ident_needs_auto(self):
        self.rmp.state.atc_mode = "STBY"
        self.s.press("CRZ", "LSK3L")
        self.assertEqual(self.row(13).strip(), "ATC MODE AUTO REQD")
        self.assertEqual(self.be.calls, [])

    def test_tcas_traffic_and_xpdr_sys(self):
        self.s.press("CRZ", "LSK2R", "LSK3R", "LSK1L")
        self.assertEqual((self.rmp.state.tcas, self.rmp.state.traffic, self.rmp.state.xpdr_sys), ("TA ONLY", "ABV", 2))
        self.s.press("LSK2R", "LSK2R", "LSK3R", "LSK3R")
        self.assertEqual((self.rmp.state.tcas, self.rmp.state.traffic), ("TA/RA", "NORM"))   # A350: NORM/ABV/BLW
        self.assertEqual(self.be.calls[:3], [("set_tcas", "TA ONLY"), ("set_traffic", "ABV"), ("set_xpdr_sys", 2)])

    def test_vhf_squawk_shortcut(self):
        self.s.press("1", "2", "0", "0", "LSK5R")
        self.assertTrue(self.row(10).rstrip().endswith("1200"))

    def test_messages_fit_24_columns(self):
        import re
        with open(R.__file__, encoding="utf-8") as f:
            src = f.read()
        literals = re.findall(r'(?:show_message|notes\.append|pg\.center\(\d+,)\s*\(?\s*f?"([^"{]+)"', src)
        self.assertTrue(literals)
        for text in literals:
            self.assertLessEqual(len(text), 24, text)

    def test_fits_screen(self):
        for name in ("vhf", "nav", "ls", "vor", "adf", "atc", "menu"):
            self.rmp.app.go(name)
            for r in range(14):
                self.assertEqual(len(self.row(r)), 24)


class FakeRequests:
    def __init__(self, values):
        self.values, self.sets = values, []

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.sets.append((key, value))


class FakeLVars:
    def __init__(self, values=None):
        self.values, self.sets = dict(values or {}), []

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.sets.append((name, value))


class FakeEvents:
    def __init__(self):
        self.fired = []

    def find(self, name):
        return lambda *a: self.fired.append((name,) + a)


class MsfsTests(unittest.TestCase):
    def test_conversions(self):
        self.assertEqual(M.com_to_hz("118.705"), 118_705_000)
        self.assertEqual(M.mhz_to_channel(118.7), "118.700")
        self.assertEqual(M.mhz_to_channel(118.00833), "118.010")   # real 8.33 frequency -> channel name
        self.assertEqual(M.mhz_to_channel(118.01667), "118.015")
        self.assertEqual(M.mhz_to_channel(121.505), "121.505")
        self.assertEqual(M.bcd(2000), 0x2000)
        self.assertEqual(M.bcd_to_squawk(0x7700), "7700")
        self.assertIsNone(M.bcd_to_squawk(0x1289))
        self.assertEqual(M.nav_to_hz("110.30"), 110_300_000)

    def make(self, values=None, lvars=None, **kw):
        rq, ev, lv = FakeRequests(values or {}), FakeEvents(), FakeLVars(lvars)
        be = M.MsfsBackend(simconnect_factory=lambda: (rq, ev, lv), ls_nav_index=3, **kw)
        state = R.RadioState()
        be._state = state
        self.assertTrue(be.connect())
        self.lv = lv
        return be, state, rq, ev

    def test_writes(self):
        be, _, rq, ev = self.make()
        be.set_com_standby(2, "124.350")
        be.swap_com(1)
        be.set_tx(3)
        be.set_nav("LS", 1, "109.50")
        be.set_nav("VOR", 2, "115.80")
        be.set_crs("VOR", 1, 270)
        be.set_crs("LS", 1, 90)           # course is panel-only on the A350: nothing fired
        be.set_adf(1, "350.0")
        be.set_squawk("7700")
        be.ident()
        be.set_xpdr_state("ALT")
        be.set_tcas("TA ONLY")
        be.set_traffic("BLW")
        be.set_xpdr_sys(2)
        self.assertEqual(self.lv.sets, [("INI_RMP1_3_RECEIVE_SEL", 1), ("INI_RMP1_TRANSMIT_CHANNEL", 3),
                                        ("INI_tcas_mode_pedestal", 3),
                                        ("INI_tcas_mode", 3), ("INI_XPDR_SYS", 2)])
        self.assertEqual(ev.fired, [
            ("COM2_STBY_RADIO_SET_HZ", 124_350_000), ("COM_STBY_RADIO_SWAP",),
            ("NAV3_RADIO_SET_HZ", 109_500_000), ("NAV2_RADIO_SET_HZ", 115_800_000),
            ("ADF_COMPLETE_SET", 0x350000), ("XPNDR_SET", 0x7700), ("XPNDR_IDENT_ON",)])
        self.assertEqual(rq.sets, [])         # transponder state is read-only through MSFS
        self.assertFalse(be.can("xpdr_state"))

    def test_poll_reads_sim(self):
        values = {"COM_ACTIVE_FREQUENCY:1": 119.25, "COM_STANDBY_FREQUENCY:1": 118.00833,
                  "NAV_ACTIVE_FREQUENCY:1": 113.9, "NAV_OBS:1": 271.4, "NAV_ACTIVE_FREQUENCY:3": 109.5,
                  "TRANSPONDER_CODE:1": 0x4521}
        lvars = {"INI_tcas_mode_pedestal": 3, "INI_tcas_mode": 3, "INI_XPDR_SYS": 2, "INI_RMP1_TRANSMIT_CHANNEL": 2}
        be, state, _, _ = self.make(values, lvars)
        changes = []
        be._on_change = lambda: changes.append(1)
        be.poll()
        self.assertEqual((state.vhf[0].active, state.vhf[0].standby), ("119.250", "118.010"))
        self.assertEqual((state.vor[0].freq, state.vor[0].crs, state.ls.freq), ("113.90", 271, "109.50"))
        self.assertEqual(state.squawk, "4521")
        self.assertEqual((state.tcas, state.traffic, state.xpdr_sys, state.tx), ("TA ONLY", "BLW", 2, 2))
        self.assertEqual(state.vhf[1].active, "121.500")   # missing values leave the panel alone
        self.assertEqual(changes, [1])

    def test_poll_reads_inactive_tcas_and_transponder_state(self):
        be, state, _, _ = self.make({"TRANSPONDER_STATE:1": 1.0}, {"INI_tcas_mode_pedestal": 0.0})
        be.poll()
        self.assertEqual((state.tcas, state.atc_mode), ("STBY", "STBY"))   # LVar 0 = TCAS STBY
        be, state, _, _ = self.make({"TRANSPONDER_STATE:1": 3.0})
        be.poll()
        self.assertEqual((state.atc_mode, state.alt_rptg), ("AUTO", False))
        be, state, _, _ = self.make({"TRANSPONDER_STATE:1": 5.0})          # GND: what AUTO reads on the ground
        state.atc_mode = "STBY"
        be.poll()
        self.assertEqual(state.atc_mode, "AUTO")

    def test_tcas_encoding_observed_in_cockpit(self):
        # what the watcher logged while TA ONLY, TA/RA, STBY, AUTO were selected in the A350
        self.assertEqual(M.tcas_from_sim(3.0), "TA ONLY")
        self.assertEqual(M.tcas_from_sim(2.0), "TA/RA")
        self.assertEqual(M.tcas_from_sim(0.0), "STBY")
        self.assertIsNone(M.tcas_from_sim(1.0))      # never observed
        be, _, _, _ = self.make()
        be.set_tcas("STBY")
        be.set_tcas("TA/RA")
        self.assertEqual(self.lv.sets, [("INI_tcas_mode_pedestal", 0), ("INI_tcas_mode_pedestal", 2)])

    def test_transmit_channel_beyond_vhf_is_ignored(self):
        be, state, _, _ = self.make({}, {"INI_RMP1_TRANSMIT_CHANNEL": 5})
        be.poll()
        self.assertEqual(state.tx, 1)

    def test_rmp_number_selects_lvars(self):
        be, _, _, _ = self.make(rmp_number=2)
        be.set_tx(1)
        self.assertEqual(self.lv.sets, [("INI_RMP2_1_RECEIVE_SEL", 1), ("INI_RMP2_TRANSMIT_CHANNEL", 1)])
        self.assertEqual(be.key_panel, "FO")

    def test_key_press_input_mode(self):
        be, _, _, ev = self.make(freq_input="keys")
        self.assertEqual(be.key_sequence_for_standby(2, "124.350"),
                         ["VHF", "RSK_2", "NUM_1", "NUM_2", "NUM_4", "NUM_3", "NUM_5", "NUM_0"])
        be.set_com_standby(1, "118.700")
        be.swap_com(1)
        queued = []
        while not be._key_queue.empty():
            queued.append(be._key_queue.get())
        self.assertEqual(queued[:3], ["RMP_CPT_VHF_KEY", "RMP_CPT_RSK_1_KEY", "RMP_CPT_NUM_1_KEY"])
        self.assertEqual(queued[-2:], ["RMP_CPT_VHF_KEY", "RMP_CPT_LSK_1_KEY"])
        self.assertEqual(ev.fired, [])        # no COM events in key mode

    def test_disconnected_writes_are_ignored(self):
        be = M.MsfsBackend(simconnect_factory=lambda: (_ for _ in ()).throw(OSError("sim not running")))
        self.assertFalse(be.connect())
        be.set_squawk("1234")               # must not raise
        self.assertIn("sim not running", be.error)


if __name__ == "__main__":
    unittest.main()
