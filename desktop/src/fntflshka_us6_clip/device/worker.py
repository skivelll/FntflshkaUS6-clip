import logging
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from .client import DeviceClient
from .discovery import Discovery
from .mock import MockTransport
from ..core.protocol import DeviceNack
from .cache import SceneCache

log = logging.getLogger(__name__)


def user_error(error):
    if isinstance(error, DeviceNack):
        return {1: "Данные повредились при передаче. Попробуй ещё раз.",
                4: "На заколке не хватает памяти.",
                5: "Заколка отклонила файл. Подготовь изображение заново.",
                9: "Предыдущая загрузка не завершена. Переподключи заколку по USB."}.get(
                    error.code, "Заколка не смогла выполнить действие. Подробности в журнале.")
    if isinstance(error, (OSError, TimeoutError)):
        return "Связь с заколкой потеряна. Подключи её по USB-C."
    if isinstance(error, (ValueError, InterruptedError)):
        message = str(error)
        if any("А" <= char <= "я" or char in "ёЁ" for char in message):
            return message
        return "Не удалось проверить данные сцены или устройства. Попробуй подключиться заново."
    return "Что-то пошло не так. Подробности записаны в журнал."


class DeviceWorker(QObject):
    snapshot_ready = Signal(object)
    status = Signal(str)
    error = Signal(str)
    progress = Signal(int, int)
    upload_done = Signal(object)
    upload_failed = Signal()
    scene_done = Signal(object)
    scene_failed = Signal()
    preview_ready = Signal(object, bytes)
    preview_failed = Signal(object, str)
    preview_progress = Signal(object, int, int)
    cache_state = Signal(bool)
    cache_progress = Signal(int, int, int, int)

    def __init__(self, mock=False):
        super().__init__()
        self.mock = mock
        self.discovery = Discovery()
        self.client = None
        self.last = None
        self.timer = None
        self.cache = SceneCache()

    def preload(self, snapshot):
        self.cache.synchronize(snapshot)
        if not snapshot.capabilities.supports_scene_read:
            self.cache_state.emit(False)
            return
        missing = any(self.cache.key(snapshot, s) not in self.cache.assets for s in snapshot.scenes)
        if not missing:
            self.cache_state.emit(False)
            return
        self.cache_state.emit(True)
        self.status.emit("● загружаю сцены в память...")
        try:
            self.cache.preload(self.client, snapshot, self.preview_ready.emit,
                               self.cache_progress.emit, self.cancelled)
        finally:
            self.cache_state.emit(False)
        self.status.emit("● clip connected" + (" · ДЕМО" if self.mock else ""))

    def cancelled(self):
        return QThread.currentThread().isInterruptionRequested()

    @Slot()
    def start(self):
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        self.poll()

    @Slot()
    def poll(self):
        if self.cancelled():
            return
        try:
            if self.client is None:
                self.status.emit("● ищу clip...")
                if self.mock:
                    self.client = DeviceClient(MockTransport())
                    snapshot = self.client.snapshot()
                else:
                    result = self.discovery.find(self.cancelled)
                    if result is None:
                        self.cache_state.emit(False)
                        self.status.emit("● заколка не найдена")
                        self.snapshot_ready.emit(None)
                        return
                    self.client, snapshot = result
                self.status.emit("● clip connected" + (" · ДЕМО" if self.mock else ""))
            else:
                snapshot = self.client.snapshot()
            self.last = snapshot
            self.snapshot_ready.emit(snapshot)
            self.preload(snapshot)
        except Exception as error:
            log.exception("Disconnect or poll failure")
            self.disconnect()
            self.error.emit(user_error(error))

    def disconnect(self):
        if self.client:
            try:
                self.client.transport.close()
            except Exception:
                log.exception("Close transport")
        self.client = None
        self.last = None
        self.cache_state.emit(False)
        self.snapshot_ready.emit(None)
        self.status.emit("● заколка не найдена")

    @Slot(bytes)
    def upload(self, data):
        self.perform_upload(data)

    @Slot(object)
    def replace_image(self, request):
        data, scene_id, checksum, port = request
        if self.client is None or self.client.transport.port != port:
            self.error.emit("Подключение изменилось. Выбери сцену заново.")
            self.upload_failed.emit()
            return
        self.perform_upload(data, scene_id, checksum)

    def perform_upload(self, data, scene_id=None, checksum=None):
        if self.client is None:
            self.error.emit("Заколка не найдена. Подключи её по USB-C.")
            self.upload_failed.emit()
            return
        self.timer.stop()
        self.status.emit("● заливаю...")
        try:
            result = self.client.upload(data, self.progress.emit, self.cancelled,
                                        scene_id=scene_id, expected_checksum=checksum)
            self.last = result[2]
            self.cache.synchronize(self.last)
            uploaded = next(s for s in self.last.scenes if s.id == result[0])
            key = self.cache.key(self.last, uploaded)
            self.cache.assets[key] = data
            self.snapshot_ready.emit(self.last)
            self.preview_ready.emit(key, data)
            self.preload(self.last)
            self.status.emit("● готово :3")
            self.upload_done.emit(result)
        except Exception as error:
            log.exception("Upload failed; no automatic upload retry")
            self.disconnect()
            self.error.emit(user_error(error))
            self.upload_failed.emit()
        finally:
            self.timer.start()

    @Slot(object)
    def read_preview(self, request):
        key, cancel = request
        if cancel.is_set() or self.cancelled():
            return
        if self.client is None or self.last is None or self.last.port != key[0]:
            self.preview_failed.emit(key, "Заколка не подключена.")
            return
        self.timer.stop()
        try:
            scene = next((s for s in self.last.scenes if (s.id, s.checksum, s.width, s.height) == key[1:]), None)
            if scene is None:
                raise ValueError("Сцена изменилась. Выбери её заново.")
            data = self.client.read_scene(scene, self.last.capabilities,
                lambda done, total: self.preview_progress.emit(key, done, total),
                lambda: cancel.is_set() or self.cancelled())
            if not cancel.is_set():
                self.preview_ready.emit(key, data)
        except InterruptedError:
            pass
        except Exception as error:
            log.exception("Read preview failed")
            self.preview_failed.emit(key, user_error(error))
        finally:
            self.timer.start()

    @Slot(object)
    def change_scene(self, request):
        if self.client is None:
            self.error.emit("Заколка не найдена. Подключи её по USB-C.")
            self.scene_failed.emit()
            return
        self.timer.stop()
        self.status.emit("● обновляю сцену...")
        try:
            action, scene_id, checksum, expected_port = request[:4]
            if self.client.transport.port != expected_port:
                raise ValueError("Подключение изменилось. Выбери сцену заново.")
            if action == "rename":
                result = self.client.rename_scene(scene_id, checksum, request[4])
            elif action == "reorder":
                result = self.client.reorder_scenes(request[4])
            else:
                result = self.client.change_scene(action, scene_id, checksum)
            self.last = result[3]
            self.snapshot_ready.emit(self.last)
            self.preload(self.last)
            self.status.emit("● готово :3")
            self.scene_done.emit(result)
        except Exception as error:
            log.exception("Scene operation failed; no automatic retry")
            self.disconnect()
            self.error.emit(user_error(error))
            self.scene_failed.emit()
        finally:
            self.timer.start()

    @Slot()
    def stop(self):
        if self.timer:
            self.timer.stop()
        self.disconnect()
