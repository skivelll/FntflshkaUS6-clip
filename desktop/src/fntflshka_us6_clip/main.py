import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication


def configure_logging() -> Path:
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / "clip.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    def uncaught(kind, error, traceback):
        logging.getLogger(__name__).critical("Unhandled exception", exc_info=(kind, error, traceback))
    sys.excepthook = uncaught
    return folder


def main() -> int:
    parser = argparse.ArgumentParser(description="FntflshkaUS6 clip")
    parser.add_argument("--mock", action="store_true", help="Use a simulated device; never access USB")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("FntflshkaUS6 clip")
    app.setOrganizationName("FntflshkaUS6")
    logs = configure_logging()
    logging.info("Startup mock=%s", args.mock)
    from .ui.main_window import MainWindow
    app.setStyleSheet((Path(__file__).parent / "resources/theme.qss").read_text(encoding="utf-8"))
    window = MainWindow(mock=args.mock, log_folder=logs)
    window.show()
    return app.exec()
