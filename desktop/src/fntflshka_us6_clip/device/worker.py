import logging
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from .client import DeviceClient
from .discovery import Discovery
from .mock import MockTransport
from .cache import SceneCache
from .errors import user_error
from ..library.archive import from_snapshot, save
from ..library.restore import execute as execute_restore, RestoreFailure
from pathlib import Path
import uuid

log = logging.getLogger(__name__)


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
    collection_ready = Signal(object)
    collection_failed = Signal(str)
    restore_done = Signal(object)
    restore_failed = Signal(str)

    def __init__(self, mock=False):
        super().__init__()
        self.mock = mock
        self.discovery = Discovery()
        self.client = None
        self.last = None
        self.timer = None
        self.cache = SceneCache()
        self.mock_transport = MockTransport() if mock else None

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
        self.status.emit("● заколка подключена" + (" · ДЕМО" if self.mock else ""))

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
                self.status.emit("● ищу заколку...")
                if self.mock:
                    self.mock_transport.connected = True
                    self.client = DeviceClient(self.mock_transport)
                    snapshot = self.client.snapshot()
                else:
                    result = self.discovery.find(self.cancelled, self.status.emit)
                    if result is None:
                        self.cache_state.emit(False)
                        self.status.emit("● заколка не найдена")
                        self.snapshot_ready.emit(None)
                        self.error.emit(self.discovery.last_error or "Заколка не найдена. Подключи её по USB-C.")
                        return
                    self.client, snapshot = result
                self.status.emit("● заколка подключена" + (" · ДЕМО" if self.mock else ""))
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

    @Slot()
    def reconnect(self):
        """Queued on the serial thread; never retries an interrupted upload."""
        if self.cancelled():
            return
        if self.timer:
            self.timer.stop()
        self.disconnect()
        self.poll()
        if self.timer and not self.cancelled():
            self.timer.start()

    @Slot(bytes)
    def upload(self, data):
        self.perform_upload(data)

    @Slot(object)
    def replace_image(self, request):
        data, scene_id, checksum, port = request[:4]
        identity_changed = len(request) > 4 and (self.last is None or self.last.device_id != request[4])
        if self.client is None or self.client.transport.port != port or identity_changed:
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
            self.error.emit(user_error(error) + " Загрузка не подтверждена. После подключения проверь список сцен; автоматического повтора нет.")
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

    @Slot(object)
    def export_collection(self, request):
        baseline, title, selected_id = request
        try:
            if self.client is None or self.client.snapshot() != baseline:
                raise ValueError("Список сцен или подключение изменились. Повтори экспорт.")
            collection = from_snapshot(baseline, self.cache.assets, title, selected_id)
            self.collection_ready.emit(collection)
        except Exception as error:
            log.exception("Collection export failed")
            self.collection_failed.emit(user_error(error))

    @Slot(object)
    def restore_collection(self, request):
        plan, folder, cancel = request
        if self.timer:
            self.timer.stop()
        try:
            if self.client is None or self.client.snapshot() != plan.baseline:
                raise ValueError("Устройство или сцены изменились. Открой план заново.")
            if plan.baseline.scenes:
                backup = from_snapshot(plan.baseline, self.cache.assets, "Перед восстановлением")
                try:
                    save(Path(folder) / ("before-restore-" + uuid.uuid4().hex + ".fclip"), backup)
                except OSError as error:
                    raise ValueError("Не удалось сохранить локальную копию перед восстановлением. Проверь доступ к папке библиотеки и свободное место на компьютере. Запись на заколку не началась.") from error
            def ready(snapshot, scene, data):
                self.last = snapshot
                self.cache.synchronize(snapshot)
                key = self.cache.key(snapshot, scene)
                self.cache.assets[key] = data
                self.snapshot_ready.emit(snapshot)
                self.preview_ready.emit(key, data)
            self.status.emit("● восстанавливаю коллекцию...")
            self.last = execute_restore(self.client, plan, ready, self.progress.emit,
                                        lambda: cancel.is_set() or self.cancelled())
            self.snapshot_ready.emit(self.last)
            self.preload(self.last)
            self.restore_done.emit(self.last)
            self.status.emit("● коллекция восстановлена")
        except Exception as error:
            log.exception("Collection restore stopped; no automatic retry")
            self.disconnect()
            if isinstance(error, RestoreFailure):
                message = user_error(error.error) + " " + str(error)
            else:
                message = user_error(error)
            self.error.emit(message)
            self.restore_failed.emit(message)
        finally:
            if self.timer and not self.cancelled():
                self.timer.start()

    @Slot()
    def stop(self):
        if self.timer:
            self.timer.stop()
        self.disconnect()
