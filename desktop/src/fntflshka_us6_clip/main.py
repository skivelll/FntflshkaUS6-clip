import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from PySide6.QtCore import QStandardPaths, QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox


def startup_error(error,folder):
    from .problems import problem
    from .ui.notifications import ProblemDialog
    dialog=ProblemDialog();value=problem(error,'startup',state='Подключение к заколке и запись не запускались.')
    dialog.display(value,value.report({'data_folder':str(folder)}));dialog.exec()
    return 1


def configure_logging(data_folder=None) -> Path:
    folder = (data_folder or Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation))) / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(folder / "clip.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    def uncaught(kind, error, traceback):
        logging.getLogger(__name__).critical("Unhandled exception", exc_info=(kind, error, traceback))
        from .problems import problem
        from .ui.notifications import ProblemDialog
        value=problem(error,'startup',state='Результат текущей операции нужно проверить по списку сцен. Автоматического повтора записи нет.')
        owner=next((w for w in QApplication.topLevelWidgets() if hasattr(w,'notifications') and not w.notifications.closing),None)
        if owner is not None:
            owner.notifications.present(value)
            return
        dialog=ProblemDialog(QApplication.activeWindow())
        dialog.display(value,value.report({'log_folder':str(folder)}))
        uncaught.dialog=dialog
        dialog.show();dialog.raise_()
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
    try:
        data_folder.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return startup_error(error,data_folder)
    instance_lock = QLockFile(str(data_folder / "app.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(0):
        if instance_lock.error()!=QLockFile.LockFailedError:
            return startup_error(PermissionError(f'QLockFile error: {instance_lock.error()}'),data_folder)
        QMessageBox.information(None, "FntflshkaUS6 clip", "Приложение уже открыто. Используй существующее окно; второй экземпляр не подключается к USB.")
        return 1
    try:
        logs = configure_logging(data_folder)
    except OSError as error:
        instance_lock.unlock()
        return startup_error(error,data_folder)
    logging.info("Startup mock=%s", args.mock)
    from .ui.main_window import MainWindow
    from .settings import Preferences
    try:
        app.setStyleSheet((Path(__file__).parent / "resources/theme.qss").read_text(encoding="utf-8"))
    except OSError as error:
        instance_lock.unlock()
        return startup_error(error,data_folder)
    if args.device_check:
        from .diagnostics import DeviceCheck
        device_check = DeviceCheck(args.device_check)
    window = MainWindow(mock=args.mock, log_folder=logs,
                        preferences=None if args.smoke_test or args.device_check else Preferences(data_folder / "settings.ini"))
    window.showMaximized()
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
