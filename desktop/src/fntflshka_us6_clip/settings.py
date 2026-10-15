"""Local preferences only: no scenes, assets or USB trust on disk."""
import json
import logging
from pathlib import Path
from PySide6.QtCore import QSettings, QByteArray

log = logging.getLogger(__name__)


class Preferences:
    def __init__(self, path: Path):
        self.store = QSettings(str(path), QSettings.IniFormat)

    def restore(self, window):
        geometry = self.store.value("window/geometry")
        if isinstance(geometry, QByteArray):
            window.restoreGeometry(geometry)
        try:
            placement = json.loads(self.store.value("image/placement", "{}"))
            window.controls.restore_preferences(placement)
        except (ValueError, TypeError, AttributeError):
            log.warning("Ignoring invalid placement preferences", exc_info=True)
        folder = self.store.value("image/folder", "")
        if isinstance(folder, str) and folder and Path(folder).is_dir():
            window.image_folder = folder

    def save(self, window):
        self.store.setValue("window/geometry", window.saveGeometry())
        self.store.setValue("image/placement", json.dumps(window.controls.preferences()))
        self.store.setValue("image/folder", window.image_folder)
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            log.warning("Unable to save local preferences: %s", self.store.status())
