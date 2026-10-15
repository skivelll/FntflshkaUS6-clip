from dataclasses import replace
import struct
import unittest
from unittest.mock import patch
from PIL import Image
from fntflshka_us6_clip.core.asset import encode_asset
from fntflshka_us6_clip.core.protocol import DeviceNack
from fntflshka_us6_clip.device.client import DeviceClient
from fntflshka_us6_clip.device.mock import MockTransport
from PySide6.QtWidgets import QApplication
from fntflshka_us6_clip.ui.main_window import MainWindow


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.transport = MockTransport()
        self.client = DeviceClient(self.transport)
        self.data = encode_asset(Image.new("RGB", (20, 20), "red"))
        self.client.upload(self.data)
        self.snapshot = self.client.snapshot()
        self.scene = self.snapshot.scenes[0]

    def test_read_returns_exact_asset_without_mutating_storage_or_active(self):
        progress = []
        data = self.client.read_scene(self.scene, self.snapshot.capabilities,
                                      lambda done, total: progress.append((done, total)))
        self.assertEqual(data, self.data)
        self.assertEqual(progress[-1], (len(data), len(data)))
        self.assertEqual(self.client.snapshot(), self.snapshot)

    def test_old_firmware_does_not_receive_unknown_command(self):
        with patch.object(self.transport, "request") as request:
            with self.assertRaisesRegex(ValueError, "1.1"):
                self.client.read_scene(self.scene, replace(self.snapshot.capabilities, reserved=0))
            request.assert_not_called()

    def test_wrong_offset_and_corrupt_payload_rejected(self):
        original = self.transport.request
        def wrong_offset(command, *args, **kwargs):
            result = original(command, *args, **kwargs)
            return b"\xff" * 4 + result[4:] if command == 11 else result
        with patch.object(self.transport, "request", side_effect=wrong_offset):
            with self.assertRaises(ValueError):
                self.client.read_scene(self.scene, self.snapshot.capabilities)
        def corrupt(command, *args, **kwargs):
            result = original(command, *args, **kwargs)
            return result[:-1] + bytes([result[-1] ^ 1]) if command == 11 else result
        with patch.object(self.transport, "request", side_effect=corrupt):
            with self.assertRaises(ValueError):
                self.client.read_scene(self.scene, self.snapshot.capabilities)

    def test_cancel_and_changed_scene_are_rejected(self):
        with self.assertRaises(InterruptedError):
            self.client.read_scene(self.scene, self.snapshot.capabilities, cancelled=lambda: True)
        with self.assertRaises(DeviceNack) as error:
            self.client.read_scene(replace(self.scene, checksum=self.scene.checksum ^ 1), self.snapshot.capabilities)
        self.assertEqual(error.exception.code, 9)

    def test_invalid_ranges_and_missing_scene(self):
        for scene_id, offset, count, expected in ((1, len(self.data), 1, 8), (1, 0, 0, 8),
                                                   (1, 0, 513, 8), (8, 0, 1, 10)):
            with self.assertRaises(DeviceNack) as error:
                self.transport.request(11, struct.pack("<IIIH", scene_id, self.scene.checksum, offset, count))
            self.assertEqual(error.exception.code, expected)


class PreviewSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_device_preview_is_centered_and_stale_result_ignored(self):
        client = DeviceClient(MockTransport())
        asset = encode_asset(Image.new("RGB", (2, 2), "red"))
        client.upload(asset)
        window = MainWindow(start_workers=False)
        try:
            window.on_snapshot(client.snapshot())
            window.scenes.setCurrentRow(0)
            key = window.preview_key
            window.on_scene_preview(key, asset)
            self.assertEqual(window.preview.target, (320, 172))
            self.assertEqual(window.preview.image.pixelColor(0, 0).name(), "#000000")
            self.assertEqual(window.preview.image.pixelColor(159, 85).name(), "#ff0000")
            self.assertFalse(window.controls.isEnabled())
            window.scenes.setCurrentRow(-1)
            window.on_scene_preview(key, asset)
            self.assertIsNone(window.preview_key)
            self.assertTrue(window.preview.image.isNull())
        finally:
            window.close()
