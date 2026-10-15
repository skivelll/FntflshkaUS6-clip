import struct
import unittest
from dataclasses import replace
from fntflshka_us6_clip.device.playlist import Playlist
from fntflshka_us6_clip.device.client import DeviceClient
from fntflshka_us6_clip.device.mock import MockTransport
from fntflshka_us6_clip.core.protocol import DeviceNack
from fntflshka_us6_clip.scenes.models import Scene


class PlaylistTests(unittest.TestCase):
    def setUp(self):
        self.transport = MockTransport()
        self.transport.scenes = [Scene(i, i - 1, 1, 2, 2, 100 + i, i == 1,
                                       f"scenes/{i:04d}a.bin", f"Scene {i}") for i in range(1, 4)]
        self.client = DeviceClient(self.transport)

    def test_wire_roundtrip_bounds(self):
        for value in (Playlist(), Playlist(True, 1000, (3, 1, 2)), Playlist(False, 86400000, tuple(range(1, 9)))):
            self.assertEqual(len(value.encode()), 42)
            self.assertEqual(Playlist.decode(value.encode()), value)

    def test_invalid_configs(self):
        for value in (Playlist(True), Playlist(False, 999), Playlist(False, 86400001),
                      Playlist(True, 5000, (1, 1)), Playlist(True, 5000, (9,))):
            with self.assertRaises(ValueError): value.encode()
        for offset in (0, 3, 40, 41):
            data = bytearray(Playlist().encode())
            data[offset] = 255
            with self.assertRaises(ValueError): Playlist.decode(data)
        with self.assertRaises(ValueError): Playlist.decode(b"")

    def test_save_ack_readback_preserves_scenes_and_order(self):
        before = self.client.snapshot()
        value = Playlist(True, 3000, (3, 1))
        after = self.client.set_playlist(value, before)
        self.assertEqual(self.client.get_playlist(), value)
        self.assertEqual([s.id for s in after.scenes], [1, 2, 3])
        self.assertEqual([s.id for s in after.scenes if s.active], [3])
        self.assertEqual(after.capabilities.storage_free, before.capabilities.storage_free)

    def test_manual_activation_stops(self):
        before = self.client.snapshot()
        self.client.set_playlist(Playlist(True, 5000, (1, 2)), before)
        self.client.change_scene("activate", 2, 102)
        self.assertFalse(self.client.get_playlist().enabled)

    def test_stale_checksum_or_identity_blocks_write(self):
        before = self.client.snapshot()
        for baseline in (replace(before, device_id="other"),
                         replace(before, scenes=(replace(before.scenes[0], checksum=999),))):
            with self.assertRaises(ValueError):
                self.client.set_playlist(Playlist(True, 5000, (1,)), baseline)
        self.assertFalse(self.client.get_playlist().enabled)

    def test_older_firmware_blocks(self):
        self.transport.caps = replace(self.transport.caps, reserved=3)
        with self.assertRaises(ValueError):
            self.client.set_playlist(Playlist(True, 5000, (1,)), self.client.snapshot())

    def test_firmware_rejects_wrong_checksum(self):
        payload = Playlist(True, 5000, (1,)).encode() + struct.pack("<I", 999)
        with self.assertRaises(DeviceNack): self.transport.request(16, payload)
        self.assertFalse(self.client.get_playlist().enabled)

    def test_disconnect_never_retries_write(self):
        before = self.client.snapshot()
        original = self.transport.request
        writes = []
        def request(command, payload=b"", timeout=5):
            if command in (16, 18):
                writes.append(payload)
                original(command, payload, timeout)
                raise OSError("lost ACK")
            return original(command, payload, timeout)
        self.transport.request = request
        with self.assertRaises(OSError):
            self.client.set_playlist(Playlist(True, 5000, (1,)), before)
        self.assertEqual(len(writes), 1)

    def test_transition_atomic_readback_and_legacy_gate(self):
        baseline = self.client.snapshot()
        for effect in range(6):
            value = Playlist(True, 3000, (1, 2), effect)
            self.client.set_playlist(value, baseline)
            self.assertEqual(self.client.get_playlist(), value)
            self.assertEqual(Playlist.decode(self.transport.request(15)).transition, 0)
        self.transport.caps = replace(self.transport.caps, reserved=7)
        with self.assertRaises(ValueError):
            self.client.set_playlist(Playlist(True, 3000, (1,), 1), self.client.snapshot())
        self.client.set_playlist(Playlist(True, 3000, (1,)), self.client.snapshot())
        self.assertEqual(self.client.get_playlist().transition, 0)
        with self.assertRaises(ValueError): Playlist(True, 3000, (1,), 6).encode()


if __name__ == "__main__":
    unittest.main()


class PlaylistGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_saves_order_interval_and_enable_through_worker(self):
        from unittest.mock import patch
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QMessageBox
        from PIL import Image
        from fntflshka_us6_clip.core.asset import encode_asset
        from fntflshka_us6_clip.ui.main_window import MainWindow
        from fntflshka_us6_clip.ui.playlist_dialog import PlaylistDialog
        from test_gui_flow import wait_for
        window = MainWindow(mock=True)
        window.show()
        dialog = None
        try:
            self.assertTrue(wait_for(lambda: window.snapshot is not None and not window.cache_loading))
            for count in (1, 2):
                window.request_upload.emit(encode_asset(Image.new("RGB", (2, 2), "red")))
                self.assertTrue(wait_for(lambda: len(window.snapshot.scenes) == count and len(window.scene_previews) == count))
            dialog = PlaylistDialog(window)
            dialog.show()
            self.assertTrue(wait_for(lambda: not dialog.pending))
            self.assertFalse(dialog.interval_row.isVisible())
            self.assertTrue(all(not dialog.scenes.item(i).icon().isNull()
                                for i in range(dialog.scenes.count())))
            with patch.object(window.device_worker.client, "read_scene", side_effect=AssertionError("Use RAM thumbnails")):
                dialog.enabled.setChecked(True)
                self.assertTrue(dialog.interval_row.isVisible())
                dialog.interval.setValue(5)
                QTest.mouseClick(dialog.interval_up, Qt.LeftButton)
                self.assertEqual(dialog.interval.value(), 6)
                QTest.mouseClick(dialog.interval_down, Qt.LeftButton)
                self.assertEqual(dialog.interval.value(), 5)
                dialog.interval.setValue(1)
                self.assertFalse(dialog.interval_down.isEnabled())
                QTest.mouseClick(dialog.interval_up, Qt.LeftButton)
                self.assertEqual(dialog.interval.value(), 2)
                dialog.interval.setValue(86400)
                self.assertFalse(dialog.interval_up.isEnabled())
                QTest.mouseClick(dialog.interval_down, Qt.LeftButton)
                self.assertEqual(dialog.interval.value(), 86399)
                dialog.enabled.setChecked(False)
                self.assertFalse(dialog.interval_row.isVisible())
            for i in range(dialog.scenes.count()):
                dialog.scenes.item(i).setCheckState(Qt.Checked)
            dialog.scenes.setCurrentRow(1)
            dialog.move(-1)
            dialog.interval.setValue(7)
            dialog.transition.setCurrentIndex(5)
            dialog.enabled.setChecked(True)
            with patch("fntflshka_us6_clip.ui.playlist_dialog.QMessageBox.question", return_value=QMessageBox.Yes):
                dialog.save()
            self.assertTrue(wait_for(lambda: not dialog.pending))
            self.assertEqual(window.device_worker.mock_transport.playlist, Playlist(True, 7000, (2, 1), 5))
            self.assertIn("подтверждены", dialog.message.text())
            self.assertEqual(len(window.scene_previews), 2)
        finally:
            if dialog:
                dialog.pending = False
                dialog.close()
            window.close()
            self.assertTrue(wait_for(lambda: not window.isVisible()))
