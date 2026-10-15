import logging
from pathlib import Path
from PySide6.QtCore import QObject, QThread, Signal, Slot
from .image import load_image, prepare_image

log = logging.getLogger(__name__)


class ImageWorker(QObject):
    ready = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.path = None
        self.source = None

    @Slot(int, object, object)
    def render(self, generation, path, placement):
        if QThread.currentThread().isInterruptionRequested():
            return
        try:
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
