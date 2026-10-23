"""End-to-end tests: host ScreenSession <-> SimulatedFcd, with a fake clock."""

import time
import unittest

from moza_fcd import FcdScreen, Page
from moza_fcd import protocol as p
from moza_fcd.session import ScreenSession, SessionState, SettingsChannel
from moza_fcd.sim import SimulatedFcd


class Loopback:
    """Synchronous wiring with queues, so neither side re-enters the other mid-call."""

    def __init__(self, drop_every=0):
        self.to_device, self.to_host = [], []
        self.session = ScreenSession(self.to_device.append)
        self.device = SimulatedFcd(self.to_host.append, cabin_position=1)
        self.now = 0.0
        self.drop_every = drop_every
        self._count = 0
        self.ready = False
        self.session.on_ready = lambda: setattr(self, "ready", True)
        self.errors = []
        self.session.on_error = self.errors.append

    def pump(self, seconds=0.0, step=0.05):
        end = self.now + seconds
        while True:
            for _ in range(10000):
                if not (self.to_device or self.to_host):
                    break
                if self.to_device:
                    data = self.to_device.pop(0)
                    self._count += 1
                    if self.drop_every and self._count % self.drop_every == 0:
                        continue  # simulate a lost frame
                    self.device.feed(data)
                if self.to_host:
                    self.session.feed(self.to_host.pop(0), self.now)
                self.session.tick(self.now)
            if self.now >= end:
                return
            self.now += step
            self.session.tick(self.now)


class SessionTests(unittest.TestCase):
    def connect(self, **kw):
        lb = Loopback(**kw)
        lb.session.start()
        lb.pump(SettingsChannel.COLLECTION_WINDOW_S + 1)
        return lb

    def test_full_bringup(self):
        lb = self.connect()
        s, d = lb.session, lb.device
        self.assertEqual(s.state, SessionState.RUNNING)
        self.assertTrue(lb.ready)
        self.assertEqual(d.display_mode, 1)
        self.assertEqual(s.settings.cabin_position, 1)
        self.assertEqual(s.settings.mcu_id, d.mcu_id)
        self.assertEqual(d.init_config, p.pack_mcdu_init_config()[5:])
        self.assertTrue(d.telemetry_token_echoed)
        self.assertIsNotNone(d.time_sync)
        self.assertEqual(lb.errors, [])

    def test_page_then_delta(self):
        lb = self.connect()
        page = Page().center(0, "HELLO", "green").write(13, 0, "small", "amber", small=True)
        lb.session.submit_page(page)
        lb.pump(1)
        self.assertEqual(lb.device.screen_text().splitlines()[0], page.row_text(0))
        self.assertEqual(lb.device.styles[0][10], 0x84)  # large green
        self.assertEqual(lb.device.styles[13][0], 0x06)  # small amber
        applied = lb.device.frames_applied

        page.write(5, 0, "CHANGED")
        lb.session.submit_page(page)
        lb.session.submit_page(page)  # unchanged -> no frame
        lb.pump(1)
        self.assertEqual(lb.device.frames_applied, applied + 1)
        self.assertTrue(lb.device.screen_text().splitlines()[5].startswith("CHANGED"))

    def test_page_submitted_before_ready_is_delivered(self):
        lb = Loopback()
        lb.session.submit_page(Page().write(3, 2, "EARLY"))
        lb.session.start()
        lb.pump(SettingsChannel.COLLECTION_WINDOW_S + 1)
        self.assertEqual(lb.device.screen_text().splitlines()[3][2:7], "EARLY")

    def test_force_resend_uses_increasing_sequence(self):
        lb = self.connect()
        lb.session.submit_page(Page().write(0, 0, "A"))
        lb.pump(0.5)
        for _ in range(3):
            lb.session.force_resend()
            lb.pump(0.5)
        self.assertEqual(lb.device.frames_rejected, 0)
        self.assertEqual(lb.device.frames_applied, 4)

    def test_long_unicode_page_survives_chunking(self):
        lb = self.connect()
        page = Page()
        for r in range(14):
            page.write(r, 0, "".join(chr(0x41 + (r * 7 + c) % 26) for c in range(23)) + "°", "cyan")
        lb.session.submit_page(page)
        lb.pump(2)
        self.assertEqual(lb.device.screen_text(), page.to_text())

    def test_recovers_from_lost_frames(self):
        lb = self.connect(drop_every=7)
        page = Page().center(6, "RETRANSMIT", "yellow")
        lb.session.submit_page(page)
        lb.pump(5)
        self.assertEqual(lb.device.screen_text().splitlines()[6], page.row_text(6))

    def test_heartbeat_keeps_connections(self):
        lb = self.connect()
        lb.pump(20)
        self.assertIsNotNone(lb.session.mux.connection(p.PORT_MCDU_TCP))
        self.assertEqual(lb.errors, [])

    def test_shutdown_closes_all_connections(self):
        lb = self.connect()
        lb.session.begin_shutdown(lb.now)
        lb.pump(1)
        self.assertTrue(lb.session.mux.close_complete)
        self.assertTrue(lb.session.shutdown_complete)

    def test_shutdown_falls_back_to_grace_period(self):
        lb = self.connect()
        lb.device.reply_to_fin = False
        lb.session.begin_shutdown(lb.now)
        lb.pump(1)
        self.assertFalse(lb.session.shutdown_complete)
        lb.pump(ScreenSession.SHUTDOWN_GRACE_S)
        self.assertTrue(lb.session.shutdown_complete)


class ThreadedSimTests(unittest.TestCase):
    def test_fcdscreen_against_sim(self):
        old = SettingsChannel.COLLECTION_WINDOW_S
        SettingsChannel.COLLECTION_WINDOW_S = 0.3
        try:
            with FcdScreen(port="sim") as screen:
                self.assertTrue(screen.wait_ready(5))
                screen.show(Page().center(6, "THREADED", "magenta"))
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and "THREADED" not in screen.simulator.screen_text():
                    time.sleep(0.05)
                self.assertIn("THREADED", screen.simulator.screen_text())
        finally:
            SettingsChannel.COLLECTION_WINDOW_S = old


if __name__ == "__main__":
    unittest.main()
