"""Bundled asynchronous GUI smoke check, strictly on the protocol mock."""
import json
import logging
from pathlib import Path
import tempfile
import time
from PIL import Image
from PySide6.QtCore import QObject, QTimer
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
