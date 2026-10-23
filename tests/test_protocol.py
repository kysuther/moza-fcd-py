"""Golden-vector tests. Expected bytes come from MobiFlight's MobiFlightMoza unit tests,
which in turn cite examples from MOZA's protocol guide."""

import unittest

from moza_fcd import protocol as p


def H(s: str) -> bytes:
    return bytes.fromhex(s)


class SerialLinkTests(unittest.TestCase):
    def test_fixed_startup_frames(self):
        self.assertEqual(p.encode_serial_frame(0x00, 0x12), H("7e 00 00 12 9d"))
        self.assertEqual(p.encode_serial_frame(0x80, 0x21), H("7e 00 80 21 2c"))
        self.assertEqual(p.tunnel_wrap(0x00), H("7e 01 43 12 00 e1"))
        self.assertEqual(p.encode_serial_frame(0xC3, 0x21, b"\x80"), H("7e 01 c3 21 80 f0"))
        self.assertEqual(p.ROOT_HANDSHAKE_REQUEST, H("7e 00 00 12 9d"))
        self.assertEqual(p.DEVICE_INIT_REQUEST, H("7e 01 43 12 00 e1"))

    def test_escaping_counts_checksum_twice(self):
        self.assertEqual(p.encode_serial_frame(0x43, 0x12, H("7c 23 3c 01 7e")),
                         H("7e 05 43 12 7c 23 3c 01 7e 7e bd"))

    def test_heartbeat_and_fin_frames(self):
        self.assertEqual(p.encode_serial_frame(0x43, 0x12, H("7c 12 34 01 37 12 00 00 00 00")),
                         H("7e 0a 43 12 7c 12 34 01 37 12 00 00 00 00 f6"))
        self.assertEqual(p.encode_serial_frame(0x43, 0x12, H("7c 12 34 00 37 12")),
                         H("7e 06 43 12 7c 12 34 00 37 12 f1"))

    def test_payload_too_long(self):
        with self.assertRaises(ValueError):
            p.encode_serial_frame(0x43, 0x12, bytes(65))

    def test_decoder_roundtrip_with_garbage_and_split(self):
        frames = [p.encode_serial_frame(0x43, 0x21, H("7c 23 3c 01 7e")), p.ROOT_HANDSHAKE_RESPONSE]
        stream = b"\x01\x02" + frames[0] + b"\x7e\x99" + frames[1]  # garbage + a bogus frame start
        dec = p.SerialLinkDecoder()
        out = []
        for b in stream:
            out += dec.feed(bytes([b]))
        self.assertEqual([(m.command, m.device_pair, m.payload) for m in out],
                         [(0x43, 0x21, H("7c 23 3c 01 7e")), (0x80, 0x21, b"")])

    def test_decoder_rejects_bad_checksum(self):
        bad = bytearray(p.ROOT_HANDSHAKE_RESPONSE)
        bad[-1] ^= 0xFF
        self.assertEqual(p.SerialLinkDecoder().feed(bytes(bad)), [])


class TunnelTests(unittest.TestCase):
    def test_unwrap_reply(self):
        msgs = p.SerialLinkDecoder().feed(H("7e 05 c3 21 fc 12 34 34 12 fc"))
        t = p.tunnel_unwrap(msgs[0])
        self.assertEqual((t.inner_command, t.is_reply), (0x7C, True))

    def test_unwrap_request_and_non_tunnel(self):
        t = p.tunnel_unwrap(p.SerialLinkDecoder().feed(H("7e 01 43 12 00 e1"))[0])
        self.assertEqual((t.inner_command, t.is_reply), (0x00, False))
        self.assertIsNone(p.tunnel_unwrap(p.SerialLinkMessage(0x80, 0x21, b"")))
        self.assertIsNone(p.tunnel_unwrap(p.SerialLinkMessage(0x43, 0x12, b"")))


class ReliableStreamTests(unittest.TestCase):
    def test_pack_vectors(self):
        self.assertEqual(p.pack_syn(9020, p.StreamType.SYN1, 0x1237, 0x1234, 3), H("23 3c 80 37 12 34 12 fc 03"))
        self.assertEqual(p.pack_syn(0x1234, p.StreamType.SYN2, 0x1234, 0x1234, 2), H("12 34 81 34 12 34 12 fd 02"))
        self.assertEqual(p.pack_ack(0x1234, 0x1238), H("12 34 38 12"))
        self.assertEqual(p.pack_trans(0x1234, 0x1237), H("12 34 01 37 12 00 00 00 00"))
        self.assertEqual(p.pack_fin(0x1234, 0x1237), H("12 34 00 37 12"))

    def test_parse_syn1(self):
        r = p.parse_stream_request(H("23 3c 80 37 12 34 12 fc 03"))
        self.assertEqual((r.destination_port, r.type, r.isn, r.announced_port, r.version),
                         (9020, p.StreamType.SYN1, 0x1237, 0x1234, 3))

    def test_parse_errors(self):
        with self.assertRaises(p.ProtocolError):
            p.parse_stream_request(H("23 3c 80 37 12 34 12 00 03"))  # bad inverse version
        with self.assertRaises(p.ProtocolError):
            p.parse_stream_request(H("12 34 01 37 12 ff ff ff ff"))  # bad CRC

    def test_trans_roundtrip(self):
        r = p.parse_stream_request(p.pack_trans(0x1234, 0x42, b"hello"))
        self.assertEqual((r.type, r.isn, r.data), (p.StreamType.TRANS, 0x42, b"hello"))

    def test_crc(self):
        self.assertEqual(p.crc32(b"123456789"), 0xCBF43926)
        self.assertEqual(p.crc32(b""), 0)


class McduFrameTests(unittest.TestCase):
    def test_init_config(self):
        self.assertEqual(p.pack_mcdu_init_config(), H("32 0c 00 00 00 02 0e 00 18 00 02 00 00 5b 23 ee 02"))

    def test_delta_single_char(self):
        frame = p.pack_mcdu_delta(2, [p.TextRow(0, [(0, "A")])], [p.StyleRow(0, [(0, b"\x87")])])
        self.assertEqual(frame, H("31 14 00 00 00 02 02 00 00 00 03 01 00 01 00 01 01 00 41 01 00 01 00 01 87"))

    def test_client_capability(self):
        cap = p.parse_mcdu_client_capability(H("02 01 33 23 01"))
        self.assertEqual((cap.version, cap.capabilities, cap.udp_listen_port, cap.page_index), (2, 1, 0x2333, 1))
        self.assertIsNone(p.parse_mcdu_client_capability(H("01 00 00 00 00")))
        self.assertIsNone(p.parse_mcdu_client_capability(H("02 00 00 00 02")))
        self.assertIsNone(p.parse_mcdu_client_capability(H("02 00 00 00")))

    def test_column_length_counts_utf16_units(self):
        frame = p.pack_mcdu_keyframe(1, [p.TextRow(0, [(0, "°")])], [])
        body = frame[5 + 6:]  # NetworkPackage header + version/seq/flags
        self.assertEqual(body[0], 1)  # one text row
        # row=0, nseg=1, start=0, columns=1 (one UTF-16 unit), utf8len=2
        self.assertEqual(body[1:7], H("00 01 00 01 02 00"))

    def test_large_keyframe_is_compressed_and_roundtrips(self):
        text = [p.TextRow(r, [(0, "X" * 24)]) for r in range(14)]
        styles = [p.StyleRow(r, [(0, bytes([0x87]) * 24)]) for r in range(14)]
        frame = p.pack_mcdu_keyframe(5, text, styles)
        self.assertEqual(frame[5 + 5] & 0x08, 0x08)
        pkg = p.NetworkPackageExtractor().feed(frame)[0]
        patch = p.parse_mcdu_patch(pkg)
        self.assertTrue(patch.keyframe)
        self.assertEqual(patch.sequence, 5)
        self.assertEqual(patch.text_rows[13].segments[0][1], "X" * 24)
        self.assertEqual(patch.style_rows[0].segments[0][1], bytes([0x87]) * 24)


class SettingsTests(unittest.TestCase):
    def test_setting_frames(self):
        self.assertEqual(p.pack_setting_frame(0x18, b"\x01"), H("ff 05 00 00 00 c8 1b b5 e1 18 00 00 00 01"))
        self.assertEqual(p.pack_setting_frame(0x1B, H("0a 00 00 00 fe ff ff ff")),
                         H("ff 0c 00 00 00 d3 d4 98 f1 1b 00 00 00 0a 00 00 00 fe ff ff ff"))
        self.assertEqual(p.pack_setting_frame(0x17, H("00 00 00 00 ff 11 22 33")),
                         H("ff 0c 00 00 00 9c 2a d6 d8 17 00 00 00 00 00 00 00 ff 11 22 33"))

    def test_time_sync_and_version(self):
        self.assertEqual(p.pack_time_sync(1735689600, 28800),
                         H("ff 10 00 00 00 ef 8f 62 73 02 00 00 00 80 85 74 67 00 00 00 00 80 70 00 00"))
        self.assertEqual(p.pack_protocol_version(3, 0),
                         H("ff 0c 00 00 00 03 28 c2 81 07 00 00 00 03 00 00 00 00 00 00 00"))

    def test_handshake_parser(self):
        full = H("07 01 00 00 00 00 02 03 aa bb 01 04 0a")
        self.assertIsNone(p.parse_settings_handshake(full[:7]))
        self.assertIsNone(p.parse_settings_handshake(full[:10]))
        mcu, ids, consumed = p.parse_settings_handshake(full)
        self.assertEqual((mcu, ids, consumed), (H("aa bb"), H("01 04 0a"), len(full)))
        with self.assertRaises(p.ProtocolError):
            p.parse_settings_handshake(H("00 01 00 00 00 00 00 00"))
        with self.assertRaises(p.ProtocolError):
            p.parse_settings_handshake(H("07 01 00 00 00 01 00 00"))

    def test_extractor_mixed_formats(self):
        ex = p.SettingsFrameExtractor()
        ex.expect_legacy_current_values()
        legacy = b"\x01" + b"\x11" * 4 + b"\x05\x22" + b"\x04" + b"\x33" * 8 + b"\x06\x44"
        data = legacy + b"\x05\x55" + p.pack_setting_frame(0x13, b"\x01")
        frames = []
        for i in range(0, len(data), 3):  # arbitrary splits
            frames += ex.feed(data[i : i + 3])
        self.assertEqual([(f.setting_id, f.data) for f in frames],
                         [(1, b"\x11" * 4), (5, b"\x22"), (4, b"\x33" * 8), (6, b"\x44"), (5, b"\x55"), (0x13, b"\x01")])


if __name__ == "__main__":
    unittest.main()
