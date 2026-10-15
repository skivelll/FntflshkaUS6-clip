import logging
from pathlib import Path
from PySide6.QtCore import QObject, QThread, Signal, Slot
from .image import load_image, prepare_image, SceneSource, scene_canvas
from .animation import GifSource, load_gif, prepare_gif, prepare_cached
from ..core.asset import parse_asset

log = logging.getLogger(__name__)


class ImageWorker(QObject):
    ready = Signal(int, object)
    failed = Signal(int, str)
    decoded = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.path = None
        self.source = None

    @Slot(object, bytes)
    def decode_preview(self, key, data):
        from .animation import preview_frames
        try:
            self.decoded.emit(key, preview_frames(data))
        except Exception:
            log.exception("Animation cache preview failed")

    @Slot(int, object, object)
    def render(self, generation, path, placement):
        if QThread.currentThread().isInterruptionRequested():
            return
        try:
            if isinstance(path, GifSource):
                stat = path.path.stat()
                key = ("gif", path.path, stat.st_mtime_ns, stat.st_size)
                if self.path != key:
                    self.source, self.path = load_gif(path.path), key
                self.ready.emit(generation, prepare_gif(self.source, path, placement))
                return
            if isinstance(path, SceneSource) and parse_asset(path.asset).get("type") == 2:
                self.ready.emit(generation, prepare_cached(path, placement))
                return
            if isinstance(path, SceneSource):
                if self.path != path:
                    self.source, self.path = scene_canvas(path), path
                    log.info("Editing cached scene pixels size=%s", self.source.size)
            else:
                image_path = Path(path)
                stat = image_path.stat()
                cache_key = (image_path, stat.st_mtime_ns, stat.st_size)
                if self.path != cache_key:
                    source = load_image(image_path)
                    self.source, self.path = source, cache_key
                    log.info("Image imported %s size=%s", path, source.size)
            result = prepare_image(self.source, placement)
            self.ready.emit(generation, result)
        except Exception as error:
            log.exception("Image preparation failed")
            message = str(error) if isinstance(error, ValueError) else "Не удалось открыть картинку. Выбери другой файл."
            self.failed.emit(generation, message)
