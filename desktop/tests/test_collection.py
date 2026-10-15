from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from fntflshka_us6_clip.core.asset import encode_asset
from fntflshka_us6_clip.device.client import DeviceClient
from fntflshka_us6_clip.device.mock import MockTransport
from fntflshka_us6_clip.device.cache import SceneCache
from fntflshka_us6_clip.ui.main_window import MainWindow
from test_gui_flow import wait_for


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.transport = MockTransport()
        self.transport.identity_key = "USB-SERIAL-A"
        self.client = DeviceClient(self.transport)
        self.data = encode_asset(Image.new("RGB", (10, 10), "red"))
        self.client.upload(self.data)
        self.client.upload(self.data)

    def preload(self, cache, snapshot=None):
        emitted = []
        cache.preload(self.client, snapshot or self.client.snapshot(), lambda key, data: emitted.append(key), lambda *args: None)
        return emitted

    def test_preload_once_and_rename_reorder_do_not_reload_assets(self):
        cache = SceneCache()
        self.assertEqual(len(self.preload(cache)), 2)
        scene = self.client.snapshot().scenes[0]
        self.client.rename_scene(scene.id, scene.checksum, 'Розовый "clip"')
        records = [(s.id, s.checksum) for s in reversed(self.client.snapshot().scenes)]
        self.client.reorder_scenes(records)
        with patch.object(self.client, "read_scene", side_effect=AssertionError("Pixels must stay cached")):
            self.assertEqual(self.preload(cache), [])
        self.assertEqual(self.client.snapshot().scenes[1].title, 'Розовый "clip"')

    def test_replacement_preserves_id_title_order_and_invalidates_only_one_asset(self):
        cache = SceneCache()
        self.preload(cache)
        first = self.client.snapshot().scenes[0]
        self.client.rename_scene(first.id, first.checksum, "Новое имя")
        data = encode_asset(Image.new("RGB", (10, 10), "blue"))
        after = self.client.upload(data, scene_id=first.id, expected_checksum=first.checksum)[2]
        changed = next(s for s in after.scenes if s.id == first.id)
        self.assertEqual((changed.id, changed.title, changed.order), (first.id, "Новое имя", first.order))
        self.assertTrue(changed.active)
        self.assertEqual(len(after.scenes), 2)
        self.assertEqual(len(self.preload(cache, after)), 1)

    def test_different_usb_identity_invalidates_all_cached_assets(self):
        cache = SceneCache()
        self.preload(cache)
        self.transport.identity_key = "USB-SERIAL-B"
        self.assertEqual(len(self.preload(cache)), 2)

    def test_stale_replacement_reorder_and_long_name_do_not_mutate(self):
        before = self.client.snapshot()
        first = before.scenes[0]
        with self.assertRaises(ValueError):
            self.client.upload(self.data, scene_id=first.id, expected_checksum=first.checksum ^ 1)
        with self.assertRaises(ValueError):
            self.client.reorder_scenes([(first.id, first.checksum)] * 2)
        with self.assertRaises(ValueError):
            self.client.rename_scene(first.id, first.checksum, "Я" * 25)
        self.assertEqual(self.client.snapshot(), before)

    def test_interrupted_replacement_keeps_previous_scene(self):
        before = list(self.transport.scenes)
        self.transport.disconnect_after = 512
        larger = encode_asset(Image.new("RGB", (40, 40), "blue"))
        with self.assertRaises(OSError):
            self.client.upload(larger, scene_id=1, expected_checksum=before[0].checksum)
        self.assertEqual(self.transport.scenes, before)
        self.assertEqual(self.transport.assets[1], self.data)


class CollectionGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cached_selection_rename_order_and_replacement(self):
        window = MainWindow(mock=True)
        window.show()
        try:
            self.assertTrue(wait_for(lambda: window.snapshot is not None and not window.cache_loading))
            data = encode_asset(Image.new("RGB", (2, 2), "red"))
            for count in (1, 2):
                window.request_upload.emit(data)
                self.assertTrue(wait_for(lambda: len(window.snapshot.scenes) == count and not window.cache_loading))
            self.assertEqual(len(window.scene_previews), 2)
            with patch.object(window.device_worker.client, "read_scene", side_effect=AssertionError("Selection must not read USB")):
                for row in (0, 1, 0, 1, 0):
                    window.scenes.setCurrentRow(row)
                    self.app.processEvents()
                    self.assertFalse(window.preview.image.isNull())
                with patch("fntflshka_us6_clip.ui.main_window.QInputDialog.getText", return_value=("Моя сцена", True)):
                    QTest.mouseClick(window.rename_button, Qt.LeftButton)
                self.assertTrue(wait_for(lambda: not window.busy and window.snapshot.scenes[0].title == "Моя сцена"))
                QTest.mouseClick(window.down_button, Qt.LeftButton)
                self.assertTrue(wait_for(lambda: not window.busy and window.snapshot.scenes[1].id == 1))
            self.assertEqual(window.selected_scene_id(), 1)
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "replace.png"
                Image.new("RGB", (320, 172), "blue").save(path)
                with patch("fntflshka_us6_clip.ui.main_window.QFileDialog.getOpenFileName", return_value=(str(path), "")):
                    QTest.mouseClick(window.replace_button, Qt.LeftButton)
                self.assertTrue(wait_for(lambda: window.prepared is not None))
                self.assertIn("ЗАМЕНИТЬ", window.upload_button.text())
                with patch("fntflshka_us6_clip.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes):
                    QTest.mouseClick(window.upload_button, Qt.LeftButton)
                self.assertTrue(wait_for(lambda: not window.busy and window.progress.value() == 100))
                current = next(s for s in window.snapshot.scenes if s.id == 1)
                self.assertEqual(current.title, "Моя сцена")
                self.assertEqual(current.order, 1)
                self.assertEqual(len(window.snapshot.scenes), 2)
        finally:
            window.close()
            self.assertTrue(wait_for(lambda: not window.isVisible()))
