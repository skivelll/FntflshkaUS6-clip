from pathlib import Path
import tempfile
import unittest
import os
import subprocess
import sys
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from fntflshka_us6_clip.settings import Preferences
from fntflshka_us6_clip.ui.main_window import MainWindow
from fntflshka_us6_clip.device.errors import user_error
from fntflshka_us6_clip.device.discovery import Discovery, port_fingerprint
from fntflshka_us6_clip.device.client import DeviceClient
from fntflshka_us6_clip.device.mock import MockTransport
from fntflshka_us6_clip.core.protocol import DeviceNack
from fntflshka_us6_clip.core.asset import encode_asset
from PIL import Image
from serial import SerialException
from PySide6.QtCore import QLockFile
from test_device import port
from test_gui_flow import wait_for


class DailyUseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_roundtrip_and_invalid_values(self):
        with tempfile.TemporaryDirectory() as folder:
            prefs = Preferences(Path(folder) / "settings.ini")
            w = MainWindow(start_workers=False, preferences=prefs)
            w.controls.mode.setCurrentIndex(1)
            w.controls.scale.setValue(137)
            w.controls.x.setValue(-17)
            w.controls.pixel_art.setChecked(True)
            w.controls.background = "#ff00ab"
            w.image_folder = folder
            w.resize(1100, 720)
            w.close()
            second = MainWindow(start_workers=False, preferences=Preferences(Path(folder) / "settings.ini"))
            self.assertEqual(second.controls.preferences(), w.controls.preferences())
            self.assertEqual(second.image_folder, folder)
            # Qt clamps restored geometry to the current screen (offscreen is 800px wide).
            self.assertTrue(prefs.store.contains("window/geometry"))
            self.assertEqual(second.height(), w.height())
            self.assertLessEqual(second.width(), w.width())
            second.controls.restore_preferences({"scale": 99999, "x": "oops", "pixel_art": "false", "background": "oops"})
            self.assertEqual(second.controls.scale.value(), 137)
            self.assertTrue(second.controls.pixel_art.isChecked())
            second.close()
            prefs.store.setValue("image/placement", "{broken")
            third = MainWindow(start_workers=False, preferences=prefs)
            self.assertEqual(third.controls.scale.value(), 100)
            third.close()

    def test_busy_port_diagnostic_never_resets_even_trusted_device(self):
        candidate = port()
        discovery = Discovery(lambda: [candidate], lambda name: (_ for _ in ()).throw(PermissionError("Access denied")))
        discovery.trusted.add(port_fingerprint(candidate))
        with patch("fntflshka_us6_clip.device.discovery.reset_serial_lines") as reset:
            self.assertIsNone(discovery.find())
            self.assertIn("COM-порт занят", discovery.last_error)
            reset.assert_not_called()

    def test_second_process_exits_before_creating_usb_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            lock = QLockFile(str(Path(folder) / "app.lock"))
            self.assertTrue(lock.tryLock(0))
            script = '''
import sys
from fntflshka_us6_clip import main as entry
from fntflshka_us6_clip.ui.main_window import MainWindow
folder = sys.argv[1]
sys.argv = ["clip"]
entry.QStandardPaths.writableLocation = lambda location: folder
entry.QMessageBox.information = lambda *args: print("instance-blocked")
def forbidden(*args, **kwargs):
    raise AssertionError("Second instance must never create USB worker")
MainWindow.__init__ = forbidden
raise SystemExit(entry.main())
'''
            try:
                result = subprocess.run([sys.executable, "-c", script, folder],
                    capture_output=True, text=True, timeout=20,
                    env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("instance-blocked", result.stdout)
                self.assertNotIn("AssertionError", result.stderr)
            finally:
                lock.unlock()

    def test_error_messages_distinguish_timeout_cancel_space_and_storage(self):
        self.assertIn("не отвечает", user_error(TimeoutError()))
        self.assertIn("остановлена", user_error(InterruptedError()))
        self.assertIn("не хватает памяти", user_error(DeviceNack(4)))
        self.assertIn("не форматировалась", user_error(DeviceNack(11)))
        self.assertIn("Связь", user_error(SerialException("WriteFile failed (PermissionError(13, 'Устройство не опознает команду.', None, 22))")))
        self.assertIn("COM-порт занят", user_error(SerialException("could not open port: PermissionError(13, 'Отказано в доступе.', None, 5)")))

    def test_manual_reconnect_keeps_cache_and_blocks_conflicting_actions(self):
        w = MainWindow(mock=True)
        w.show()
        try:
            self.assertTrue(wait_for(lambda: w.snapshot is not None and not w.cache_loading))
            w.request_upload.emit(encode_asset(Image.new("RGB", (2, 2), "blue")))
            self.assertTrue(wait_for(lambda: len(w.scene_previews) == 1))
            old = w.snapshot.scenes
            with patch.object(DeviceClient, "read_scene", side_effect=AssertionError("Cache must survive reconnect")):
                w.reconnect_device()
                self.assertFalse(w.reconnect_button.isEnabled())
                self.assertFalse(w.add_button.isEnabled())
                self.assertTrue(wait_for(lambda: w.snapshot is not None and not w.cache_loading))
                self.assertEqual(w.snapshot.scenes, old)
                w.scenes.setCurrentRow(0)
                self.assertFalse(w.preview.image.isNull())
            w.busy = True
            w.update_scene_actions()
            self.assertFalse(w.reconnect_button.isEnabled())
            w.busy = False
        finally:
            w.close()
            self.assertTrue(wait_for(lambda: not w.isVisible()))

    def test_unconfirmed_upload_warning_survives_connection_changes(self):
        w = MainWindow(start_workers=False)
        w.busy = True
        warning = "Загрузка не подтверждена. Проверь список сцен."
        w.on_device_error(warning)
        w.busy = False
        w.on_device_error("Заколка не найдена. Подключи её по USB-C.")
        self.assertIn(warning, w.message.text())
        w.on_snapshot(DeviceClient(MockTransport()).snapshot())
        self.assertIn(warning, w.message.text())
        w.close()

    def test_worker_reconnect_after_disconnect_does_not_retry_upload(self):
        from fntflshka_us6_clip.device.worker import DeviceWorker
        worker = DeviceWorker(mock=True)
        worker.start()
        try:
            worker.client.upload(encode_asset(Image.new("RGB", (2, 2), "red")))
            worker.poll()  # Cache the committed scene before the failed new upload.
            before = list(worker.mock_transport.scenes)
            worker.mock_transport.disconnect_after = 512
            messages, failures = [], []
            worker.error.connect(messages.append)
            worker.upload_failed.connect(lambda: failures.append(True))
            worker.perform_upload(encode_asset(Image.new("RGB", (40, 40), "blue")))
            self.assertEqual(failures, [True])
            self.assertIn("Загрузка не подтверждена", messages[-1])
            self.assertEqual(worker.mock_transport.scenes, before)
            with patch.object(DeviceClient, "upload", side_effect=AssertionError("No automatic retry")):
                worker.reconnect()
                self.assertEqual(worker.last.scenes, tuple(before))
        finally:
            worker.stop()
