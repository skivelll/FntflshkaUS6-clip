from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image
from fntflshka_us6_clip.device.client import DeviceClient
from fntflshka_us6_clip.device.discovery import Discovery, ordered_ports, port_fingerprint
from fntflshka_us6_clip.device.mock import MockTransport
from fntflshka_us6_clip.core.protocol import DeviceNack
from fntflshka_us6_clip.core.asset import encode_asset


def port(name="COM7", serial="unique", vid=0x303A, pid=0x1001):
    return SimpleNamespace(device=name, serial_number=serial, vid=vid, pid=pid)


class DeviceTests(unittest.TestCase):
    def test_upload_verifies_active_crc_and_refreshes_storage(self):
        transport = MockTransport()
        progress = []
        result = DeviceClient(transport).upload(encode_asset(Image.new("RGB", (10, 5), "red")),
                                                lambda done, total: progress.append((done, total)))
        scene_id, before, after = result
        self.assertEqual(scene_id, 1)
        self.assertTrue(after.scenes[0].active)
        self.assertEqual(after.capabilities.storage_free, before.capabilities.storage_free - 124)
        self.assertEqual(progress[-1], (124, 124))
        self.assertEqual(DeviceClient(transport).upload(encode_asset(Image.new("RGB", (1, 1), "blue")))[0], 2)

    def test_no_space_is_rejected_before_begin(self):
        transport = MockTransport(free=8192)
        with self.assertRaisesRegex(ValueError, "памяти"):
            DeviceClient(transport).upload(encode_asset(Image.new("RGB", (1, 1))))
        self.assertIsNone(transport.upload)
        with self.assertRaises(DeviceNack) as error:
            transport.request(5, b"\x01\x00\x00\x00" + (100).to_bytes(4, "little") + b"\0" * 4)
        self.assertEqual(error.exception.code, 4)

    def test_disconnect_never_commits_partial_scene(self):
        transport = MockTransport(disconnect_after=512)
        with self.assertRaises(OSError):
            DeviceClient(transport).upload(encode_asset(Image.new("RGB", (40, 40))))
        self.assertEqual(transport.scenes, [])

    def test_corrupt_asset_rejected_before_begin(self):
        transport = MockTransport()
        data = bytearray(encode_asset(Image.new("RGB", (1, 1))))
        data[-1] ^= 1
        with self.assertRaises(ValueError):
            DeviceClient(transport).upload(bytes(data))
        self.assertIsNone(transport.upload)

    def test_discovery_prioritizes_hint_but_accepts_identity_elsewhere(self):
        other, hint = port("COM9", vid=1, pid=2), port()
        self.assertEqual(ordered_ports([other, hint]), [hint, other])
        device = MockTransport()
        discovery = Discovery(lambda: [other], lambda name: device)
        self.assertIsNotNone(discovery.find())

    def test_unknown_matching_vid_does_not_authorize_reset(self):
        discovery = Discovery(lambda: [port()], lambda name: MockTransport())
        with patch.object(DeviceClient, "snapshot", side_effect=TimeoutError), \
             patch("fntflshka_us6_clip.device.discovery.reset_serial_lines") as reset:
            self.assertIsNone(discovery.find())
            reset.assert_not_called()

    def test_wrong_identity_is_rejected_even_on_previously_trusted_port(self):
        candidate = port()
        discovery = Discovery(lambda: [candidate], lambda name: MockTransport())
        discovery.trusted.add(port_fingerprint(candidate))
        with patch.object(DeviceClient, "snapshot", side_effect=ValueError("unexpected firmware identity")), \
             patch("fntflshka_us6_clip.device.discovery.reset_serial_lines") as reset:
            self.assertIsNone(discovery.find())
            reset.assert_not_called()
            self.assertNotIn(port_fingerprint(candidate), discovery.trusted)

    def test_recovery_requires_same_serial_and_prior_successful_handshake(self):
        candidate = port()
        discovery = Discovery(lambda: [candidate], lambda name: MockTransport())
        self.assertIsNotNone(discovery.find())
        snapshot = DeviceClient(MockTransport()).snapshot()
        with patch.object(DeviceClient, "snapshot", side_effect=[TimeoutError(), snapshot]), \
             patch("fntflshka_us6_clip.device.discovery.reset_serial_lines") as reset, \
             patch("fntflshka_us6_clip.device.discovery.time.sleep"):
            self.assertIsNotNone(discovery.find())
            reset.assert_called_once()
        changed = port(serial="different")
        discovery.enumerate_ports = lambda: [changed]
        with patch.object(DeviceClient, "snapshot", side_effect=TimeoutError), \
             patch("fntflshka_us6_clip.device.discovery.reset_serial_lines") as reset:
            self.assertIsNone(discovery.find())
            reset.assert_not_called()

    def test_all_scene_slots_full_does_not_overwrite(self):
        transport = MockTransport()
        client = DeviceClient(transport)
        data = encode_asset(Image.new("RGB", (1, 1)))
        for _ in range(8):
            client.upload(data)
        with self.assertRaisesRegex(ValueError, "8"):
            client.upload(data)
        self.assertEqual(len(transport.scenes), 8)
