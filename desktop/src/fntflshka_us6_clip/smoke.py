"""Bundled asynchronous GUI smoke check, strictly on the protocol mock."""
import json
import logging
from pathlib import Path
import tempfile
import time
from PIL import Image
from PySide6.QtCore import QObject, QTimer, QPoint, QPointF, QEvent, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication
from .core.transport import ClipDevice
from .device.client import DeviceClient
from .device.discovery import Discovery

log = logging.getLogger(__name__)


class SmokeCheck(QObject):
    def __init__(self, window, report):
        super().__init__(window)
        self.window = window
        self.report = Path(report).resolve()
        self.temp = tempfile.TemporaryDirectory(prefix="clip-smoke-")
        self.started = time.monotonic()
        self.step = 0
        self.ticks = 0
        self.reads = 0
        self.results = []
        self.failed = None
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self.tick)

    def start(self):
        # Any accidental discovery/real transport access turns the smoke red.
        def forbidden(*args, **kwargs):
            raise AssertionError("Mock smoke must never access serial hardware")
        Discovery.find = forbidden
        ClipDevice.__init__ = forbidden
        original = DeviceClient.read_scene
        def read_scene(client, *args, **kwargs):
            self.reads += 1
            return original(client, *args, **kwargs)
        DeviceClient.read_scene = read_scene
        self.timer.start()

    def tick(self):
        self.ticks += 1
        w = self.window
        try:
            if time.monotonic() - self.started > 60:
                raise TimeoutError(f"Smoke timed out at step {self.step}")
            if self.step == 0 and w.snapshot and not w.cache_loading:
                assert w.snapshot.port == "ДЕМО"
                path = Path(self.temp.name) / "картинка.png"
                Image.new("RGB", (200, 200), "red").save(path)
                w.import_image(path)
                self.step = 1
            elif self.step == 1 and w.prepared:
                assert w.prepared.preview.getpixel((0, 0)) == (0, 0, 0)
                self.results.append("import_fit_rgb565")
                w.controls.mode.setCurrentIndex(1)
                self.step = 2
            elif self.step == 2 and w.prepared:
                assert w.prepared.preview.getpixel((0, 0)) == (255, 0, 0)
                self.results.append("fill_rgb565")
                w.begin_upload()
                self.step = 3
            elif self.step == 3 and not w.busy and len(w.snapshot.scenes) == 1:
                w.begin_upload()
                self.step = 4
            elif self.step == 4 and not w.busy and len(w.snapshot.scenes) == 2:
                assert len(w.scene_previews) == 2
                self.results.append("two_uploads_ack_active_cache")
                reads = self.reads
                for row in (0, 1, 0, 1, 0):
                    w.scenes.setCurrentRow(row)
                    assert not w.preview.image.isNull()
                assert self.reads == reads
                self.results.append("cached_selection_no_reads")
                w.begin_scene_action("rename", w.selected_scene(), 'Тест "exe"')
                self.step = 5
            elif self.step == 5 and not w.busy:
                assert w.snapshot.scenes[0].title == 'Тест "exe"'
                w.move_selected(1)
                self.step = 6
            elif self.step == 6 and not w.busy:
                assert w.snapshot.scenes[1].id == 1
                self.results.append("rename_reorder")
                scene = w.snapshot.scenes[1]
                self.replaced_checksum = scene.checksum
                path = Path(self.temp.name) / "замена.png"
                Image.new("RGB", (320, 172), "blue").save(path)
                w.import_image(path, replacement=(scene.id, scene.checksum, w.snapshot.port))
                self.step = 7
            elif self.step == 7 and w.prepared:
                w.busy = True
                w.update_scene_actions()
                w.request_replace.emit((w.prepared.asset, *w.replace_target))
                self.step = 8
            elif self.step == 8 and not w.busy:
                scene = w.snapshot.scenes[1]
                assert scene.id == 1 and scene.title == 'Тест "exe"'
                assert scene.checksum != self.replaced_checksum and scene.active
                self.results.append("replacement_preserves_title_order")
                w.scenes.setCurrentRow(1)
                w.edit_selected()
                self.step = 11
            elif self.step == 11 and w.prepared:
                rect = w.preview.display_rect()
                start = rect.center()
                end = start + QPointF(20 * rect.width() / 320, 8 * rect.height() / 172)
                for kind, point, button, buttons in (
                    (QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton),
                    (QEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton),
                    (QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton),
                ):
                    event = QMouseEvent(kind, point, QPointF(w.preview.mapToGlobal(point.toPoint())), button, buttons, Qt.NoModifier)
                    QApplication.sendEvent(w.preview, event)
                assert (w.controls.x.value(), w.controls.y.value()) == (20, 8)
                w.controls.scale_spin.setValue(137)
                assert w.controls.scale.value() == 137
                event = QWheelEvent(start, QPointF(w.preview.mapToGlobal(start.toPoint())), QPoint(), QPoint(0, 120),
                                    Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
                QApplication.sendEvent(w.preview, event)
                assert w.controls.scale.value() == w.controls.scale_spin.value() == 151
                self.results.append("editor_drag_numeric_wheel")
                self.edit_checksum = w.snapshot.scenes[1].checksum
                w.controls.scale_spin.setValue(80)  # Expose margins, so the rendered pixels must change.
                self.step = 12
            elif self.step == 12 and w.prepared:
                assert w.storage_bar.projection.upload == len(w.prepared.asset)
                self.report.parent.mkdir(parents=True, exist_ok=True)
                w.grab().save(str(self.report.with_name(self.report.stem + "-editor.png")))
                w.busy = True
                w.update_scene_actions()
                w.request_replace.emit((w.prepared.asset, *w.replace_target, w.replace_device_id))
                self.step = 13
            elif self.step == 13 and not w.busy:
                assert w.source_asset is None and len(w.snapshot.scenes) == 2
                assert w.snapshot.scenes[1].id == 1 and w.snapshot.scenes[1].title == 'Тест "exe"'
                assert w.snapshot.scenes[1].checksum != self.edit_checksum
                assert w.storage_bar.projection.reclaim == 110104
                self.results.append("cached_scene_edit_same_id_storage_bar")
                self.reads_before_reconnect = self.reads
                w.reconnect_device()
                self.step = 9
            elif self.step == 9 and w.snapshot and not w.cache_loading:
                assert len(w.snapshot.scenes) == 2
                assert w.snapshot.scenes[1].title == 'Тест "exe"'
                assert self.reads == self.reads_before_reconnect
                self.results.append("reconnect_preserves_assets_cache")
                w.scenes.setCurrentRow(1)
                w.begin_scene_action("delete", w.selected_scene())
                self.step = 10
            elif self.step == 10 and not w.busy:
                assert len(w.snapshot.scenes) == 1
                assert w.snapshot.scenes[0].id == 2
                self.results.append("delete_ack_refresh")
                w.library_folder = Path(self.temp.name) / "library"
                w.open_library()
                self.step = 14
            elif self.step == 14 and not w.library_dialog.io_busy:
                w.library_dialog.title.setText("Smoke backup :3")
                w.library_dialog.backup(None)
                self.step = 15
            elif self.step == 15 and not w.library_dialog.io_busy and not w.library_dialog.serial_busy and w.library_dialog.collection:
                self.lib_collection = w.library_dialog.collection
                self.lib_archive = Path(self.temp.name) / "копия.fclip"
                w.library_dialog.file_job("export", (self.lib_collection, self.lib_archive))
                self.step = 16
            elif self.step == 16 and not w.library_dialog.io_busy and self.lib_archive.exists():
                self.results.append("library_export_archive")
                w.library_dialog.file_job("import", self.lib_archive)
                self.step = 17
            elif self.step == 17 and not w.library_dialog.io_busy and w.library_dialog.collection:
                dialog = w.library_dialog
                assert dialog.collections.count() == 2
                before = w.snapshot
                w.on_snapshot(None)
                assert not dialog.preview.image.isNull() and not dialog.restore_button.isEnabled()
                w.on_snapshot(before)
                self.results.append("library_import_offline_preview")
                self.report.parent.mkdir(parents=True, exist_ok=True)
                dialog.grab().save(str(self.report.with_name(self.report.stem + "-library.png")))
                from .library.restore import make_plan
                dialog.begin_restore(make_plan(self.lib_collection, w.snapshot, ["add"]))
                self.step = 18
            elif self.step == 18 and not w.busy and not w.library_dialog.serial_busy and not w.library_dialog.io_busy:
                assert len(w.snapshot.scenes) == 2
                assert any(s.id == 1 and s.active for s in w.snapshot.scenes)
                assert list(w.library_folder.glob("before-restore-*.fclip"))
                assert self.reads == 0
                self.results.append("library_restore_with_prebackup_no_reads")
                self.finish()
        except Exception as error:
            log.exception("Bundled smoke failed")
            self.failed = repr(error)
            self.finish()

    def finish(self):
        self.timer.stop()
        payload = {"ok": self.failed is None, "checks": self.results, "error": self.failed,
                   "event_loop_ticks": self.ticks, "asset_reads": self.reads,
                   "seconds": round(time.monotonic() - self.started, 3), "hardware_access": False}
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.window.grab().save(str(self.report.with_suffix(".png")))
        self.report.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        QApplication.instance().setQuitOnLastWindowClosed(False)
        self.window.close()
        self.shutdown = QTimer(self)
        self.shutdown.setInterval(50)
        self.shutdown.timeout.connect(self.wait_for_close)
        self.shutdown.start()

    def wait_for_close(self):
        if not self.window.isVisible():
            self.shutdown.stop()
            self.temp.cleanup()
            QApplication.instance().exit(1 if self.failed else 0)
