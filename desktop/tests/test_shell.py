import unittest
from PySide6.QtWidgets import QApplication
from fntflshka_us6_clip.ui.main_window import MainWindow
from fntflshka_us6_clip.device.mock import MockTransport
from fntflshka_us6_clip.device.models import Capabilities
from fntflshka_us6_clip.core.protocol import parse_hello


class ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_window_opens_with_landscape_preview(self):
        window = MainWindow(mock=True, start_workers=False)
        window.show()
        self.app.processEvents()
        self.assertTrue(window.isVisible())
        self.assertEqual(window.preview.target, (320, 172))
        self.assertFalse(window.upload_button.isEnabled())
        window.close()

    def test_mock_handshake_and_capabilities(self):
        mock = MockTransport()
        self.assertEqual(parse_hello(mock.request(1)), (1, 0))
        caps = Capabilities.decode(mock.request(2))
        self.assertEqual((caps.display_width, caps.display_height), (320, 172))
