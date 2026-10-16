import unittest
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication
from fntflshka_us6_clip.ui.main_window import MainWindow


class ThreadLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_close_waits_for_native_cleanup_even_if_not_running(self):
        window=MainWindow(mock=True,start_workers=False)
        thread=Mock();thread.isRunning.return_value=False;thread.wait.side_effect=[False,True]
        window.device_thread=thread
        try:
            event=Mock();window.closeEvent(event)
            thread.wait.assert_called_once_with(0);event.ignore.assert_called_once();event.accept.assert_not_called()
            thread.requestInterruption.assert_called_once();thread.quit.assert_called_once()
            event=Mock();window.closeEvent(event);event.accept.assert_called_once()
        finally:window.device_thread=None;window.close()
