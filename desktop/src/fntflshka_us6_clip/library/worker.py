import logging
from pathlib import Path
import uuid
from PySide6.QtCore import QObject, QThread, Signal, Slot
from .archive import load, save
from ..media.image import scene_canvas, SceneSource
from ..problems import problem

log = logging.getLogger(__name__)


class LibraryWorker(QObject):
    listed = Signal(object)
    loaded = Signal(object, object)
    saved = Signal(object)
    error = Signal(str)
    failure = Signal(object)
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
                from ..core.asset import parse_asset
                from ..media.animation import preview_frames
                previews = [preview_frames(s.asset, c.display) if parse_asset(s.asset).get("type") == 2
                            else scene_canvas(SceneSource(s.asset, c.display)).convert("RGB") for s in c.scenes]
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
            self.failure.emit(problem(error,'library',context={'action':request[0],'folder':str(self.folder),
                'file_path':str(request[1]) if request[0] in ('load','import') else str(request[1][1]) if request[0]=='export' else None}))
            if isinstance(error, ValueError):
                message = str(error) if any("А" <= c <= "я" or c in "ёЁ" for c in str(error)) else "Архив не прошёл проверку целостности или формата. Выбери другую копию."
            else:
                message = "Не удалось прочитать или записать архив. Проверь путь и свободное место на компьютере."
            self.error.emit(message)
        finally:
            self.done.emit()
