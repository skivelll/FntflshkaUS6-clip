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
                assert w.isMaximized() and w.columns.count()==3
                assert w.scene_panel.isAncestorOf(w.scenes)
                assert w.scene_actions_panel.isAncestorOf(w.upload_button)
                assert w.settings_panel.isAncestorOf(w.controls)
                self.results.append('maximized_three_column_workspace')
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
                from .core.capacity import allocated_size
                assert w.storage_bar.projection.upload == allocated_size(len(w.prepared.asset))
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
                assert w.storage_bar.projection.reclaim == 110592
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
                w.library_dialog.close()
                from .ui.playlist_dialog import PlaylistDialog
                self.playlist_dialog = PlaylistDialog(w)
                self.playlist_dialog.show()
                self.step = 19
            elif self.step == 19 and not self.playlist_dialog.pending:
                from .device.playlist import Playlist
                dialog = self.playlist_dialog
                assert dialog.save_button.isEnabled() and dialog.scenes.count() == 2
                self.playlist_value = Playlist(True, 3000, tuple(s.id for s in reversed(w.snapshot.scenes)), 5)
                dialog.pending = True
                w.request_playlist.emit((self.playlist_value, w.snapshot))
                self.step = 20
            elif self.step == 20 and not self.playlist_dialog.pending:
                dialog = self.playlist_dialog
                assert dialog.enabled.isChecked() and dialog.interval.value() == 3
                assert dialog.interval_row.isVisible() and dialog.transition.currentIndex() == 5
                assert all(not dialog.scenes.item(i).icon().isNull() for i in range(dialog.scenes.count()))
                dialog.interval_up.click()
                assert dialog.interval.value() == 4
                dialog.interval_down.click()
                assert dialog.interval.value() == 3
                assert dialog.scenes.item(0).data(Qt.UserRole) == self.playlist_value.ids[0]
                assert self.reads == 0
                self.results.append("playlist_ack_readback_order_no_asset_reads")
                dialog.grab().save(str(self.report.with_name(self.report.stem + "-playlist.png")))
                dialog.close()
                path = Path(self.temp.name) / "анимация.gif"
                Image.new("RGB", (32, 32), "red").save(path, save_all=True,
                    append_images=[Image.new("RGB", (32, 32), "blue")], duration=100, loop=0)
                w.import_image(path, gif_options=("compact", 0, 2, 1))
                self.step = 21
            elif self.step == 21 and w.prepared:
                from .core.asset import parse_asset
                info = parse_asset(w.prepared.asset)
                assert info["type"] == 2 and info["width"] == 128 and len(w.preview.animation) == 2
                w.gif_options = ("full", 0, 2, 1)
                w.source_revision += 1
                w.schedule_render()
                self.step = 22
            elif self.step == 22 and w.prepared:
                from .core.asset import parse_asset
                assert parse_asset(w.prepared.asset)["width"] == 320
                w.begin_upload()
                self.step = 23
            elif self.step == 23 and not w.busy and len(w.snapshot.scenes) == 3:
                assert w.snapshot.scenes[-1].type == 2
                w.scenes.setCurrentRow(2)
                self.step = 24
            elif self.step == 24 and len(w.preview.animation) == 2:
                assert self.reads == 0
                assert w.snapshot.capabilities.supports_library_budget
                assert w.snapshot.capabilities.library_limit == 820 * 1024
                assert w.storage_bar.projection.library
                assert "Библиотека:" in w.memory.text()
                self.results.append("fixed_library_budget_and_allocation_preview")
                self.results.append("gif_both_profiles_upload_cached_animation")
                w.grab().save(str(self.report.with_name(self.report.stem + "-animation.png")))
                w.open_library()
                self.step = 25
            elif self.step == 25 and not w.library_dialog.io_busy:
                w.library_dialog.title.setText("Animation smoke")
                w.library_dialog.backup(3)
                self.step = 26
            elif self.step == 26 and not w.library_dialog.io_busy and not w.library_dialog.serial_busy and w.library_dialog.collection:
                assert len(w.library_dialog.preview.animation) == 2
                from .library.restore import make_plan
                w.library_dialog.begin_restore(make_plan(w.library_dialog.collection, w.snapshot, ["add"]))
                self.step = 27
            elif self.step == 27 and not w.busy and not w.library_dialog.serial_busy and not w.library_dialog.io_busy:
                assert len(w.snapshot.scenes) == 4 and w.snapshot.scenes[-1].type == 2
                assert self.reads == 0
                self.results.append("animation_library_preview_restore_no_reads")
                from .problems import problem
                from .core.protocol import DeviceNack
                w.notify_error(TimeoutError('bundled diagnostic timeout'),'upload')
                notice=w.notifications.dialog
                assert notice.isVisible() and 'не подтверждено' in notice.state.text()
                notice.toggle.setChecked(True)
                assert notice.details.isVisible() and 'TimeoutError' in notice.details.toPlainText()
                w.notifications.present(problem(DeviceNack(4),'scene'))
                assert len(w.notifications.pending)==1
                assert not w.error_sound.isChecked()
                self.results.append('friendly_error_details_report_no_usb')
                self.notice_ticks=self.ticks
                notice.accept();self.step=28
            elif self.step==28 and w.notifications.last.operation=='scene' and w.notifications.dialog.isVisible():
                assert self.ticks>self.notice_ticks
                assert 'nack_code' in w.notifications.dialog.details.toPlainText()
                self.results.append('error_queue_nonblocking_event_loop')
                w.notifications.dialog.accept()
                self.quality_path=Path(self.temp.name)/'quality.png'
                Image.linear_gradient('L').resize((640,344)).convert('RGB').save(self.quality_path)
                w.import_image(self.quality_path)
                w.controls.pixel_art.setChecked(False)
                w.controls.dithering.setChecked(True)
                self.step=29
            elif self.step==29 and w.prepared:
                assert w.prepared.before is not None and w.compare_button.isEnabled()
                assert w.prepared.preview.tobytes()!=w.prepared.before.tobytes()
                from .ui.comparison import ComparisonDialog
                dialog=ComparisonDialog(w.prepared.before,w.prepared.preview,w);dialog.show();dialog.close()
                self.quality_bytes=w.prepared.asset
                self.project_path=Path(self.temp.name)/'quality.fclipimage'
                w.request_project.emit(('save',self.project_path,w.image_source,w.controls.placement()))
                self.results.append('static_quality_exact_preview_compare')
                self.step=30
            elif self.step==30 and self.project_path.exists():
                w.request_project.emit(('load',self.project_path,None,None))
                self.step=31
            elif self.step==31 and w.source_path==self.project_path and w.prepared:
                assert w.prepared.asset==self.quality_bytes
                assert self.reads==0
                self.results.append('local_source_project_worker_roundtrip_no_usb')
                w.new_text_scene();w.text_controls.add();w.text_controls.text.setPlainText('Привет :3')
                self.step=32
            elif self.step==32 and w.prepared:
                assert w.prepared.before is not None and w.preview.interactive
                assert w.prepared.preview.getbbox() is not None
                self.text_scene_count=len(w.snapshot.scenes)
                w.begin_upload();self.step=33
            elif self.step==33 and not w.busy and len(w.snapshot.scenes)==self.text_scene_count+1:
                self.results.append('static_text_upload_ack_active_no_reads')
                w.text_controls.speed.setValue(120);w.text_controls.motion.setCurrentIndex(1)
                assert w.text_animation_pending and w.prepared is None and not w.upload_button.isEnabled()
                self.results.append('text_gesture_draft_waits_for_final_asset')
                self.step=34
            elif self.step==34 and w.prepared and w.prepared.frames:
                assert w.prepared.frames[0][0].tobytes()==w.prepared.frames[-1][0].tobytes()
                w.begin_upload();self.step=35
            elif self.step==35 and w.snapshot and not w.busy and len(w.snapshot.scenes)==self.text_scene_count+2:
                assert w.snapshot.scenes[-1].type==2 and self.reads==0
                self.results.append('scroll_text_upload_loop_seam_no_reads')
                w.new_drawing_scene();w.draw_controls.set_color('#58c7ff');w.draw_controls.size.setValue(4)
                w.draw_controls.start(20,20);w.draw_controls.move(80,80);w.draw_controls.finish()
                w.draw_controls.set_color('#7566d9');w.draw_controls.tool.setCurrentIndex(2);w.draw_controls.start(300,160)
                self.step=36
            elif self.step==36 and not w.draw_controls.fill_busy and w.prepared:
                assert len(w.render_placement().paint_pixels)==320*172*4
                self.drawing_asset=w.prepared.asset;self.drawing_path=Path(self.temp.name)/'drawing.fclipimage'
                w.request_project.emit(('save',self.drawing_path,w.image_source,w.render_placement()))
                self.results.append('drawing_stroke_async_fill_exact_preview');self.step=37
            elif self.step==37 and self.drawing_path.exists():
                w.request_project.emit(('load',self.drawing_path,None,None));self.step=38
            elif self.step==38 and w.source_path==self.drawing_path and w.prepared:
                assert w.prepared.asset==self.drawing_asset
                self.results.append('drawing_project_worker_roundtrip')
                self.drawing_scene_count=len(w.snapshot.scenes);w.begin_upload();self.step=39
            elif self.step==39 and w.snapshot and not w.busy and len(w.snapshot.scenes)==self.drawing_scene_count+1:
                assert self.reads==0 and w.snapshot.scenes[-1].active
                self.results.append('drawing_upload_ack_active_no_usb')
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
