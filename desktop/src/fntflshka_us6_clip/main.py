import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from PySide6.QtCore import QStandardPaths, QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox


def configure_logging(data_folder=None) -> Path:
    folder = (data_folder or Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))) / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / "clip.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    def uncaught(kind, error, traceback):
        logging.getLogger(__name__).critical("Unhandled exception", exc_info=(kind, error, traceback))
        QMessageBox.critical(None, "FntflshkaUS6 clip", "Произошла ошибка приложения. Подробности записаны в журнал. Проверь список сцен перед повторной загрузкой.")
    sys.excepthook = uncaught
    return folder


def main() -> int:
    parser = argparse.ArgumentParser(description="FntflshkaUS6 clip")
    parser.add_argument("--mock", action="store_true", help="Use a simulated device; never access USB")
    parser.add_argument("--smoke-test", type=Path, metavar="REPORT.json", help="Run bundled GUI checks and exit; requires --mock")
    parser.add_argument("--device-check", type=Path, metavar="REPORT.json", help="Read-only device/cache/reconnection check and exit")
    args = parser.parse_args()
    if args.smoke_test and not args.mock:
        parser.error("--smoke-test requires --mock; hardware is never used for automated smoke checks")
    if args.device_check and (args.mock or args.smoke_test):
        parser.error("--device-check uses the real device and cannot be combined with mock checks")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("FntflshkaUS6 clip")
    app.setOrganizationName("FntflshkaUS6")
    data_folder = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))
    if args.mock:
        data_folder /= "smoke" if args.smoke_test else "demo"
    data_folder.mkdir(parents=True, exist_ok=True)
    instance_lock = QLockFile(str(data_folder / "app.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(0):
        QMessageBox.information(None, "FntflshkaUS6 clip", "Приложение уже открыто. Используй существующее окно; второй экземпляр не подключается к USB.")
        return 1
    logs = configure_logging(data_folder)
    logging.info("Startup mock=%s", args.mock)
    from .ui.main_window import MainWindow
    from .settings import Preferences
    app.setStyleSheet((Path(__file__).parent / "resources/theme.qss").read_text(encoding="utf-8"))
    if args.device_check:
        from .diagnostics import DeviceCheck
        device_check = DeviceCheck(args.device_check)
    window = MainWindow(mock=args.mock, log_folder=logs,
                        preferences=None if args.smoke_test or args.device_check else Preferences(data_folder / "settings.ini"))
    window.show()
    if args.smoke_test:
        from .smoke import SmokeCheck
        smoke = SmokeCheck(window, args.smoke_test)
        smoke.start()
    if args.device_check:
        device_check.start(window)
    try:
        return app.exec()
    finally:
        instance_lock.unlock()
