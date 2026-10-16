from dataclasses import replace
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from fntflshka_us6_clip.core.asset import encode_asset, parse_asset
from fntflshka_us6_clip.device.client import DeviceClient
from fntflshka_us6_clip.device.models import mock_capabilities
from fntflshka_us6_clip.media.image import SceneSource, Placement, prepare_image, scene_canvas
from fntflshka_us6_clip.scenes.models import storage_projection, StorageProjection, Scene
from fntflshka_us6_clip.ui.main_window import MainWindow
from fntflshka_us6_clip.ui.storage_bar import StorageBar
from fntflshka_us6_clip.settings import Preferences
from test_gui_flow import wait_for


def wheel(widget, position, delta=120):
    event = QWheelEvent(QPointF(position), QPointF(widget.mapToGlobal(position)), QPoint(), QPoint(0, delta),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


class EditorMediaTests(unittest.TestCase):
    def test_cached_pixels_at_100_percent_match_current_scene(self):
        image = Image.new("RGB", (320, 172), "#b94a79")
        image.paste("blue", (10, 20, 70, 90))
        asset = encode_asset(image)
        prepared = prepare_image(scene_canvas(SceneSource(asset, (320, 172))), Placement(pixel_art=True))
        self.assertEqual(prepared.asset, asset)

    def test_small_scene_stays_at_original_pixel_size_until_edited(self):
        asset = encode_asset(Image.new("RGB", (2, 2), "red"))
        prepared = prepare_image(scene_canvas(SceneSource(asset, (320, 172))), Placement(pixel_art=True))
        self.assertEqual(prepared.preview.getpixel((159, 85)), (255, 0, 0))
        self.assertEqual(prepared.preview.getpixel((158, 85)), (0, 0, 0))
        self.assertEqual(len(prepared.asset), 110104)
        broken = bytearray(asset)
        broken[-1] ^= 1
        with self.assertRaises(ValueError):
            scene_canvas(SceneSource(bytes(broken), (320, 172)))

    def test_storage_forecast_is_explicitly_logical_not_double_subtracted(self):
        caps = mock_capabilities()
        uploaded = storage_projection(caps, upload=110104)
        self.assertEqual(uploaded.projected_free, caps.storage_free - 110104)
        scene = Scene(1, 0, 1, 320, 172, 0, True, "scenes/0001a.bin")
        deleted = storage_projection(caps, scene=scene)
        self.assertEqual(deleted.reclaim, 110104)
        self.assertEqual(deleted.projected_free, caps.storage_free + 110104)
        self.assertEqual(storage_projection(replace(caps, storage_free=caps.storage_total), scene=scene).projected_free, caps.storage_total)


class EditorGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(mock=True)
        self.window.show()
        self.assertTrue(wait_for(lambda: self.window.snapshot is not None and not self.window.cache_loading))
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.window.close()
        self.assertTrue(wait_for(lambda: not self.window.isVisible()))
        self.temp.cleanup()

    def import_red(self):
        path = Path(self.temp.name) / "red.png"
        Image.new("RGB", (320, 172), "red").save(path)
        self.window.import_image(path)
        self.assertTrue(wait_for(lambda: self.window.prepared is not None))

    def create_scene(self):
        w = self.window
        self.import_red()
        w.begin_upload()
        self.assertTrue(wait_for(lambda: not w.busy and len(w.snapshot.scenes) == 1))
        w.scenes.setCurrentRow(0)
        w.begin_scene_action("rename", w.selected_scene(), "Моя сцена")
        self.assertTrue(wait_for(lambda: not w.busy and w.snapshot.scenes[0].title == "Моя сцена"))

    def test_drag_uses_lcd_pixels_and_works_after_window_resize(self):
        self.import_red()
        w = self.window
        for size in ((1180, 800), (1320, 900)):
            w.resize(*size)
            self.app.processEvents()
            w.controls.set_offset(0, 0)
            rect = w.preview.display_rect()
            start = rect.center().toPoint()
            delta = QPoint(round(32 * rect.width() / 320), round(15 * rect.height() / 172))
            QTest.mousePress(w.preview, Qt.LeftButton, pos=start)
            QTest.mouseMove(w.preview, start + delta)
            QTest.mouseRelease(w.preview, Qt.LeftButton, pos=start + delta)
            self.assertAlmostEqual(w.controls.x.value(), 32, delta=1)
            self.assertAlmostEqual(w.controls.y.value(), 15, delta=1)
            self.assertTrue(wait_for(lambda: w.prepared is not None))
            self.assertEqual(w.prepared.preview.getpixel((0, 86)), (0, 0, 0))
            self.assertEqual(w.prepared.preview.getpixel((60, 86)), (255, 0, 0))

    def test_typed_scale_wheel_anchor_and_disabled_preview(self):
        self.import_red()
        w = self.window
        w.controls.scale_spin.setFocus()
        w.controls.scale_spin.selectAll()
        QTest.keyClicks(w.controls.scale_spin, "137")
        QTest.keyClick(w.controls.scale_spin, Qt.Key_Return)
        self.assertEqual(w.controls.scale.value(), 137)
        self.assertTrue(wait_for(lambda: w.prepared is not None))
        rect = w.preview.display_rect()
        position = (rect.center() + QPointF(40 * rect.width() / 320, 0)).toPoint()
        wheel(w.preview, position)
        self.assertEqual(w.controls.scale.value(), 151)
        self.assertEqual(w.controls.scale_spin.value(), 151)
        self.assertAlmostEqual(w.controls.x.value(), -4, delta=1)
        wheel(w.preview, QPoint(0, 0))
        self.assertEqual(w.controls.scale.value(), 151)
        w.busy = True
        w.update_scene_actions()
        wheel(w.preview, position)
        self.assertEqual(w.controls.scale.value(), 151)
        self.assertFalse(w.preview.interactive)
        w.busy = False

    def test_preview_updates_during_held_drag_and_continuous_wheel(self):
        self.import_red()
        w = self.window
        from fntflshka_us6_clip.media import worker as image_worker
        original = image_worker.prepare_image
        calls = []
        def slower(source, placement):
            calls.append(placement)
            time.sleep(0.08)  # Force newer events while each previous frame is in flight.
            return original(source, placement)
        with patch.object(image_worker, "prepare_image", side_effect=slower):
            for gesture in ("drag", "wheel"):
                if gesture == "wheel":
                    w.controls.scale_spin.setValue(50)
                    self.assertTrue(wait_for(lambda: w.prepared is not None))
                point = w.preview.display_rect().center().toPoint()
                if gesture == "drag":
                    QTest.mousePress(w.preview, Qt.LeftButton, pos=point)
                frames, events = [], []
                initial = w.preview.image.cacheKey()
                timer = QTimer()
                def move():
                    events.append(True)
                    if gesture == "drag":
                        QTest.mouseMove(w.preview, point + QPoint(len(events) * 2, 0))
                    else:
                        wheel(w.preview, point, 120 if len(events) % 2 else -120)
                    if w.preview.image.cacheKey() != initial:
                        frames.append((w.preview.image.cacheKey(), w.prepared is None))
                timer.timeout.connect(move)
                timer.start(10)
                try:
                    self.assertTrue(wait_for(lambda: len(events) >= 40
                                             and len({key for key, stale in frames}) >= 2
                                             and any(stale for key, stale in frames)))
                    self.assertGreaterEqual(len({key for key, stale in frames}), 2, gesture)
                    self.assertTrue(any(stale for key, stale in frames), gesture)
                    if gesture == "drag":
                        self.assertIsNotNone(w.preview._drag_origin)
                finally:
                    timer.stop()
                    if gesture == "drag":
                        QTest.mouseRelease(w.preview, Qt.LeftButton, pos=point + QPoint(len(events) * 2, 0))
                self.assertTrue(wait_for(lambda: w.prepared is not None))
            self.assertLess(len(calls), 40)  # Events are coalesced; no unbounded render queue.

    def test_previous_source_intermediate_frame_is_not_displayed_after_import(self):
        self.import_red()
        w = self.window
        from fntflshka_us6_clip.media import worker as image_worker
        original = image_worker.prepare_image
        def slower(source, placement):
            time.sleep(0.1)
            return original(source, placement)
        with patch.object(image_worker, "prepare_image", side_effect=slower):
            w.controls.x.setValue(20)
            w.dispatch_render()
            self.assertTrue(w.render_inflight)
            path = Path(self.temp.name) / "blue.png"
            Image.new("RGB", (320, 172), "blue").save(path)
            w.import_image(path)
            w.preview.clear_image()
            colors = []
            timer = QTimer()
            timer.timeout.connect(lambda: colors.append(w.preview.image.pixelColor(160, 86).name()) if not w.preview.image.isNull() else None)
            timer.start(10)
            try:
                self.assertTrue(wait_for(lambda: w.prepared is not None))
                self.assertNotIn("#ff0000", colors)
                self.assertEqual(w.preview.image.pixelColor(160, 86).name(), "#0000ff")
            finally:
                timer.stop()

    def test_edit_cancel_and_save_use_cache_preserve_metadata_and_remain_responsive(self):
        w = self.window
        self.create_scene()
        original = w.snapshot.scenes[0]
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start(20)
        try:
            with patch.object(DeviceClient, "read_scene", side_effect=AssertionError("Editor must use RAM")):
                QTest.mouseClick(w.edit_button, Qt.LeftButton)
                self.assertTrue(wait_for(lambda: w.prepared is not None))
                self.assertEqual(parse_asset(w.prepared.asset)["payload_crc"], original.checksum)
                self.assertTrue(w.controls.isEnabled())
                w.controls.set_offset(23, 9)
                QTest.mouseClick(w.cancel_edit_button, Qt.LeftButton)
                self.assertIsNone(w.source_asset)
                self.assertTrue(wait_for(lambda: not w.render_inflight))
                self.assertIsNone(w.prepared)
                self.assertEqual(w.snapshot.scenes[0], original)
                self.assertEqual(w.selected_scene_id(), original.id)
                QTest.mouseClick(w.edit_button, Qt.LeftButton)
                self.assertTrue(wait_for(lambda: w.prepared is not None))
                w.controls.set_offset(23, 9)
                self.assertTrue(wait_for(lambda: w.prepared is not None))
                changed_crc = parse_asset(w.prepared.asset)["payload_crc"]
                self.assertNotEqual(changed_crc, original.checksum)
                with patch("fntflshka_us6_clip.ui.main_window.QMessageBox.question", return_value=QMessageBox.No) as question:
                    QTest.mouseClick(w.upload_button, Qt.LeftButton)
                    self.assertEqual(question.call_args.args[-1], QMessageBox.No)
                self.assertEqual(w.snapshot.scenes[0], original)
                before_ticks = len(ticks)
                with patch("fntflshka_us6_clip.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes):
                    QTest.mouseClick(w.upload_button, Qt.LeftButton)
                self.assertTrue(w.busy)
                self.assertFalse(w.preview.interactive)
                self.assertTrue(wait_for(lambda: not w.busy))
                after = w.snapshot.scenes[0]
                self.assertEqual((after.id, after.title, after.order), (original.id, original.title, original.order))
                self.assertEqual(after.checksum, changed_crc)
                self.assertTrue(after.active)
                self.assertEqual(len(w.snapshot.scenes), 1)
                self.assertIsNone(w.source_asset)
                self.assertEqual(w.selected_scene_id(), original.id)
                self.assertGreater(len(ticks) - before_ticks, 5)
        finally:
            timer.stop()

    def test_changed_usb_identity_or_scene_disables_saving_old_draft(self):
        w = self.window
        self.create_scene()
        w.edit_selected()
        self.assertTrue(wait_for(lambda: w.prepared is not None))
        original = w.snapshot
        for snapshot in (replace(original, device_id="different-usb"),
                         replace(original, scenes=(replace(original.scenes[0], checksum=1234),))):
            w.on_snapshot(snapshot)
            self.assertFalse(w.upload_button.isEnabled())
            uploads = []
            w.request_replace.connect(uploads.append)
            with patch("fntflshka_us6_clip.ui.main_window.QMessageBox.question") as question:
                w.begin_upload()
                question.assert_not_called()
            self.assertEqual(uploads, [])

    def test_memory_bar_changes_between_upload_and_delete_forecasts(self):
        w = self.window
        self.import_red()
        self.assertEqual(w.storage_bar.projection.upload, 110592)
        w.begin_upload()
        self.assertTrue(wait_for(lambda: not w.busy and len(w.snapshot.scenes) == 1))
        w.scenes.setCurrentRow(0)
        self.assertEqual(w.storage_bar.projection.reclaim, 110592)
        self.assertEqual(w.storage_bar.projection.upload, 0)
        self.assertIn("≈", w.memory.text())
        with patch("fntflshka_us6_clip.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes):
            w.delete_selected()
        self.assertTrue(wait_for(lambda: not w.busy and not w.snapshot.scenes))
        self.assertIn("изменение:", w.message.text())

    def test_edit_preferences_do_not_replace_import_defaults(self):
        w = self.window
        self.create_scene()
        w.controls.scale.setValue(137)
        w.edit_selected()
        self.assertEqual(w.controls.scale.value(), 100)
        w.controls.scale.setValue(321)
        prefs = Preferences(Path(self.temp.name) / "settings.ini")
        prefs.save(w)
        other = MainWindow(start_workers=False, preferences=prefs)
        try:
            self.assertEqual(other.controls.scale.value(), 137)
            self.assertIsNone(other.source_asset)
        finally:
            other.close()

    def test_worker_rejects_replacement_from_different_usb_identity(self):
        w = self.window
        self.create_scene()
        original = w.snapshot.scenes[0]
        failed = []
        w.device_worker.upload_failed.connect(lambda: failed.append(True))
        w.request_replace.emit((encode_asset(Image.new("RGB", (2, 2), "blue")),
                                original.id, original.checksum, w.snapshot.port, "different-usb"))
        self.assertTrue(wait_for(lambda: bool(failed)))
        self.assertEqual(w.snapshot.scenes[0], original)

    def test_memory_bar_draws_used_new_file_and_reclaim_segments(self):
        bar = StorageBar()
        bar.resize(1000, 18)
        bar.show()
        try:
            bar.set_projection(StorageProjection(1000, 600, 400, 100, 0))
            image = bar.grab().toImage()
            self.assertEqual(image.pixelColor(100, 8).name(), "#756c7e")
            # Less than the 8192-byte safety margin: incoming file is red.
            self.assertEqual(image.pixelColor(450, 8).name(), "#e46b77")
            self.assertEqual(image.pixelColor(800, 8).name(), "#30243d")
            bar.set_projection(StorageProjection(1000, 600, 400, 0, 200))
            self.assertEqual(bar.grab().toImage().pixelColor(300, 8).name(), "#65c7ba")
        finally:
            bar.close()
