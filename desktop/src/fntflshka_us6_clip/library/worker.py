import logging
from pathlib import Path
import uuid
from PySide6.QtCore import QObject, QThread, Signal, Slot
from .archive import load, save
from ..media.image import scene_canvas, SceneSource

log = logging.getLogger(__name__)


class LibraryWorker(QObject):
    listed = Signal(object)
    loaded = Signal(object, object)
    saved = Signal(object)
    error = Signal(str)
    done = Signal()

    def __init__(self, folder):
        super().__init__()
        self.folder = Path(folder)

    @Slot(object)
    def run(self, request):
        try:
            action, payload = request
            self.folder.mkdir(parents=True, exist_ok=True)
            if action == "list":
                rows = []
                for path in sorted(self.folder.glob("*.fclip"), key=lambda p: p.name, reverse=True):
                    if QThread.currentThread().isInterruptionRequested():
                        return
                    try:
                        c = load(path)
                        rows.append((path, c.title, len(c.scenes)))
                    except (OSError, ValueError):
                        log.warning("Invalid library file %s", path, exc_info=True)
                        rows.append((path, "Повреждённый архив: " + path.name, 0))
                self.listed.emit(rows)
            elif action == "load":
                c = load(payload)
                previews = [scene_canvas(SceneSource(s.asset, c.display)).convert("RGB") for s in c.scenes]
                self.loaded.emit(c, previews)
            elif action == "import":
                c = load(payload)
                path = self.folder / (uuid.uuid4().hex + ".fclip")
                save(path, c)
                self.saved.emit(path)
            elif action == "save":
                c = payload
                path = self.folder / (uuid.uuid4().hex + ".fclip")
                save(path, c)
                self.saved.emit(path)
            elif action == "export":
                c, path = payload
                save(path, c)
                self.saved.emit(Path(path))
            else:
                raise ValueError("Неизвестное действие библиотеки.")
        except Exception as error:
            log.exception("Library operation failed")
            if isinstance(error, ValueError):
                message = str(error) if any("А" <= c <= "я" or c in "ёЁ" for c in str(error)) else "Архив не прошёл проверку целостности или формата. Выбери другую копию."
            else:
                message = "Не удалось прочитать или записать архив. Проверь путь и свободное место на компьютере."
            self.error.emit(message)
        finally:
            self.done.emit()
