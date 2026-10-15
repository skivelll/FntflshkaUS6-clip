from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from fntflshka_us6_clip.ui.main_window import MainWindow


def wait_for(predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    return False


class GuiFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_add_button_preview_fit_fill_and_upload_without_blocking_ui(self):
        window = MainWindow(mock=True)
        window.show()
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start(20)
        try:
            self.assertTrue(wait_for(lambda: window.snapshot is not None and not window.cache_loading))
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "test.png"
                Image.new("RGBA", (200, 200), "red").save(path)
                with patch("fntflshka_us6_clip.ui.main_window.QFileDialog.getOpenFileName", return_value=(str(path), "")):
                    QTest.mouseClick(window.add_button, Qt.LeftButton)
                self.assertTrue(wait_for(lambda: window.prepared is not None))
                self.assertEqual(window.prepared.preview.size, (320, 172))
                self.assertEqual(window.prepared.preview.getpixel((0, 0)), (0, 0, 0))
                window.controls.mode.setCurrentIndex(1)
                self.assertTrue(wait_for(lambda: window.prepared is not None))
                self.assertEqual(window.prepared.preview.getpixel((0, 0)), (255, 0, 0))
                self.assertTrue(window.upload_button.isEnabled())
                before = len(ticks)
                QTest.mouseClick(window.upload_button, Qt.LeftButton)
                self.assertTrue(window.busy)
                self.assertTrue(wait_for(lambda: not window.busy and window.progress.value() == 100))
                self.assertGreater(len(ticks) - before, 5)
                self.assertTrue(any(scene.id == 1 and scene.active for scene in window.snapshot.scenes))
                self.assertIn("готово", window.status.text().lower())
        finally:
            timer.stop()
            window.close()
            self.assertTrue(wait_for(lambda: not window.isVisible()))
