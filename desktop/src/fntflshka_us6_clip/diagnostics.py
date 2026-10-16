"""Read-only support check for the packaged application's real USB path."""
import json
import logging
from pathlib import Path
import time
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication
from .device.client import DeviceClient


class DeviceCheck(QObject):
    def __init__(self, report):
        super().__init__()
        self.report = Path(report).resolve()
        self.reads = 0
        self.started = time.monotonic()
        self.stage = 0
        self.error = None
        self.before = None
        self.payload = {}
        original = DeviceClient.read_scene
        def read_scene(client, *args, **kwargs):
            self.reads += 1
            return original(client, *args, **kwargs)
        DeviceClient.read_scene = read_scene

    def start(self, window):
        self.window = window
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def tick(self):
        w = self.window
        try:
            if time.monotonic() - self.started > 180:
                raise TimeoutError(w.message.text())
            if self.stage == 0 and w.snapshot and not w.cache_loading:
                self.before = w.snapshot
                assert len(w.scene_previews) == len(w.snapshot.scenes), "Not all assets are cached"
                self.selection_reads = self.reads
                self.selection_row = 0
                self.stage = 2
                if w.scenes.count():
                    w.scenes.setCurrentRow(0)
            elif self.stage == 2:
                # Animation decode is asynchronous in ImageWorker. Let the Qt
                # loop deliver it before checking/advancing to the next scene.
                if self.selection_row < w.scenes.count():
                    if w.preview.image.isNull():
                        return
                    self.selection_row += 1
                    if self.selection_row < w.scenes.count():
                        w.scenes.setCurrentRow(self.selection_row)
                        return
                assert self.reads == self.selection_reads, "Selection fetched assets"
                self.payload = {"port": w.snapshot.port, "device_id": w.snapshot.device_id,
                                "firmware": w.snapshot.firmware_version,
                                "storage_total": w.snapshot.capabilities.storage_total,
                                "storage_free": w.snapshot.capabilities.storage_free,
                                "scenes": [s.__dict__ for s in w.snapshot.scenes],
                                "preloaded_assets": len(w.scene_previews), "reads_before_reconnect": self.selection_reads}
                w.reconnect_device()
                self.stage = 1
            elif self.stage == 1 and w.snapshot and not w.cache_loading:
                assert w.snapshot == self.before, "Device state changed during read-only check"
                assert self.reads == self.payload["reads_before_reconnect"], "Reconnect fetched cached assets"
                self.payload["reads_after_reconnect"] = self.reads
                self.finish()
        except Exception as error:
            logging.exception("Device support check failed")
            self.error = repr(error)
            self.finish()

    def finish(self):
        self.timer.stop()
        self.payload.update(ok=self.error is None, error=self.error,
                            seconds=round(time.monotonic() - self.started, 3), content_mutated=False)
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.report.write_text(json.dumps(self.payload, indent=2, ensure_ascii=False), encoding="utf-8")
        QApplication.instance().setQuitOnLastWindowClosed(False)
        self.window.close()
        self.shutdown = QTimer(self)
        self.shutdown.setInterval(100)
        self.shutdown.timeout.connect(self.wait_for_close)
        self.shutdown.start()

    def wait_for_close(self):
        if not self.window.isVisible():
            self.shutdown.stop()
            QApplication.instance().exit(1 if self.error else 0)
