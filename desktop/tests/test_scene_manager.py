import unittest
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from fntflshka_us6_clip.core.asset import encode_asset
from fntflshka_us6_clip.core.protocol import DeviceNack
from fntflshka_us6_clip.device.client import DeviceClient
from fntflshka_us6_clip.device.mock import MockTransport
from fntflshka_us6_clip.scenes.models import next_scene_id
from fntflshka_us6_clip.ui.main_window import MainWindow
from test_gui_flow import wait_for


class SceneClientTests(unittest.TestCase):
    def setUp(self):
        self.transport = MockTransport()
        self.client = DeviceClient(self.transport)
        self.client.upload(encode_asset(Image.new("RGB", (2, 1), "red")))
        self.client.upload(encode_asset(Image.new("RGB", (2, 1), "blue")))

    def test_activate_checks_active_and_preserves_storage(self):
        scene = self.client.snapshot().scenes[0]
        _, _, before, after = self.client.change_scene("activate", scene.id, scene.checksum)
        self.assertEqual([s.id for s in after.scenes if s.active], [scene.id])
        self.assertEqual(before.capabilities.storage_free, after.capabilities.storage_free)

    def test_delete_inactive_preserves_active_and_reclaims_slot(self):
        scene = self.client.snapshot().scenes[0]
        _, _, before, after = self.client.change_scene("delete", scene.id, scene.checksum)
        self.assertEqual([s.id for s in after.scenes], [2])
        self.assertTrue(after.scenes[0].active)
        self.assertGreater(after.capabilities.storage_free, before.capabilities.storage_free)
        self.assertEqual(next_scene_id(after.scenes), 1)

    def test_delete_active_matches_firmware_fallback(self):
        scene = self.client.snapshot().scenes[1]
        after = self.client.change_scene("delete", scene.id, scene.checksum)[3]
        self.assertFalse(any(s.active for s in after.scenes))

    def test_stale_selection_never_changes_scene(self):
        scene = self.client.snapshot().scenes[0]
        with self.assertRaisesRegex(ValueError, "изменилась"):
            self.client.change_scene("delete", scene.id, scene.checksum ^ 1)
        self.assertEqual(len(self.client.snapshot().scenes), 2)

    def test_ack_without_actual_mutation_is_rejected(self):
        scene = self.client.snapshot().scenes[0]
        original = self.transport.request
        with patch.object(self.transport, "request", side_effect=lambda command, *args, **kwargs:
                          b"" if command in (8, 9) else original(command, *args, **kwargs)):
            with self.assertRaisesRegex(ValueError, "не подтвердила"):
                self.client.change_scene("delete", scene.id, scene.checksum)
            with self.assertRaisesRegex(ValueError, "не подтвердила"):
                self.client.change_scene("activate", scene.id, scene.checksum)


class SceneGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selection_survives_refresh_and_actions_disabled_without_device(self):
        client = DeviceClient(MockTransport())
        client.upload(encode_asset(Image.new("RGB", (1, 1))))
        window = MainWindow(start_workers=False)
        try:
            window.on_snapshot(client.snapshot())
            window.scenes.setCurrentRow(0)
            window.on_snapshot(client.snapshot())
            self.assertEqual(window.selected_scene_id(), 1)
            self.assertTrue(window.delete_button.isEnabled())
            self.assertFalse(window.activate_button.isEnabled())
            window.on_snapshot(None)
            self.assertFalse(window.delete_button.isEnabled())
        finally:
            window.close()

    def test_buttons_activate_and_confirm_delete_through_worker(self):
        window = MainWindow(mock=True)
        window.show()
        try:
            self.assertTrue(wait_for(lambda: window.snapshot is not None))
            data = encode_asset(Image.new("RGB", (1, 1), "red"))
            for count in (1, 2):
                window.request_upload.emit(data)
                self.assertTrue(wait_for(lambda: len(window.snapshot.scenes) == count))
            window.scenes.setCurrentRow(0)
            QTest.mouseClick(window.activate_button, Qt.LeftButton)
            self.assertTrue(wait_for(lambda: not window.busy and window.snapshot.scenes[0].active))
            window.scenes.setCurrentRow(1)
            with patch("fntflshka_us6_clip.ui.main_window.QMessageBox.question", return_value=QMessageBox.No):
                QTest.mouseClick(window.delete_button, Qt.LeftButton)
            self.assertFalse(window.busy)
            self.assertEqual(len(window.snapshot.scenes), 2)
            with patch("fntflshka_us6_clip.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes):
                QTest.mouseClick(window.delete_button, Qt.LeftButton)
            self.assertTrue(wait_for(lambda: not window.busy and len(window.snapshot.scenes) == 1))
            self.assertTrue(window.snapshot.scenes[0].active)
        finally:
            window.close()
            self.assertTrue(wait_for(lambda: not window.isVisible()))
