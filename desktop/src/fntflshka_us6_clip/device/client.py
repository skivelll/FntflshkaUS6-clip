"""Typed operations over injectable protocol transport; shared by GUI and CLI."""
from dataclasses import replace
import logging
import struct
import zlib
from .models import Capabilities, Snapshot
from ..core.protocol import COMMANDS, parse_hello
from ..core.asset import parse_asset
from ..scenes.models import decode_scenes, next_scene_id, estimate_storage

log = logging.getLogger(__name__)


class DeviceClient:
    def __init__(self, transport):
        self.transport = transport

    def get_playlist(self):
        from .playlist import Playlist
        value = Playlist.decode(self.transport.request(COMMANDS["get_playlist"]))
        caps = Capabilities.decode(self.transport.request(COMMANDS["capabilities"]))
        if caps.supports_transitions:
            effect = self.transport.request(COMMANDS["get_transition"])
            if len(effect) != 1 or effect[0] > 5:
                raise ValueError("Неверный переход от заколки.")
            value = replace(value, transition=effect[0])
        return value

    def set_playlist(self, value, baseline):
        before = self.snapshot()
        if not before.capabilities.supports_playlist:
            raise ValueError("Для автономного плейлиста нужна firmware 1.3.")
        if before.device_id != baseline.device_id or before.port != baseline.port:
            raise ValueError("Подключение изменилось. Открой плейлист заново.")
        records = []
        for scene_id in value.ids:
            expected = next((s for s in baseline.scenes if s.id == scene_id), None)
            if expected is None:
                raise ValueError("Сцена удалена. Убери её из плейлиста.")
            self._check_scene(before, scene_id, expected.checksum)
            records.append(expected.checksum)
        payload = value.encode() + b"".join(struct.pack("<I", crc) for crc in records)
        command = COMMANDS["set_playlist"]
        if before.capabilities.supports_transitions:
            command = COMMANDS["set_playlist_effect"]
            payload += bytes([value.transition])
        elif value.transition:
            raise ValueError("Для переходов нужна firmware 1.4.")
        self.transport.request(command, payload, timeout=10)
        if self.get_playlist() != value:
            raise ValueError("Заколка не подтвердила настройки плейлиста.")
        return self.snapshot()

    def snapshot(self):
        version = parse_hello(self.transport.request(COMMANDS["hello"], timeout=2))
        self.transport.firmware_version = version
        caps = Capabilities.decode(self.transport.request(COMMANDS["capabilities"]))
        storage = self.transport.request(COMMANDS["storage"])
        if len(storage) != 8:
            raise ValueError("Invalid storage record")
        total, free = struct.unpack("<II", storage)
        if free > total:
            raise ValueError("Invalid free space")
        caps = replace(caps, storage_total=total, storage_free=free)
        scenes = decode_scenes(self.transport.request(COMMANDS["list"]))
        if caps.supports_scene_metadata:
            named = []
            for scene in scenes:
                record = self.transport.request(COMMANDS["scene_details"], struct.pack("<II", scene.id, scene.checksum))
                if not record or record[0] > 48 or len(record) != 1 + record[0]:
                    raise ValueError("Неверное имя сцены от заколки.")
                named.append(replace(scene, title=record[1:].decode("utf-8")))
            scenes = tuple(named)
        return Snapshot(self.transport.port, version, caps, scenes,
                        getattr(self.transport, "identity_key", self.transport.port))

    def rename_scene(self, scene_id, checksum, title):
        title_bytes = title.strip().encode("utf-8")
        if len(title_bytes) > 48 or any(ord(c) < 32 or ord(c) == 127 for c in title.strip()):
            raise ValueError("Название должно занимать не больше 48 байт UTF-8, без управляющих символов.")
        before = self.snapshot()
        if not before.capabilities.supports_scene_metadata:
            raise ValueError("Для названий и порядка нужна firmware 1.2.")
        self._check_scene(before, scene_id, checksum)
        self.transport.request(COMMANDS["rename"], struct.pack("<II", scene_id, checksum) + title_bytes)
        after = self.snapshot()
        if not any(s.id == scene_id and s.title == title.strip() for s in after.scenes):
            raise ValueError("Заколка не подтвердила новое название.")
        log.info("Scene rename ACK verified port=%s id=%s", before.port, scene_id)
        return "rename", scene_id, before, after

    def reorder_scenes(self, records):
        before = self.snapshot()
        if not before.capabilities.supports_scene_metadata:
            raise ValueError("Для названий и порядка нужна firmware 1.2.")
        if len(records) != len(before.scenes) or {i for i, crc in records} != {s.id for s in before.scenes}:
            raise ValueError("Список сцен изменился. Повтори перестановку.")
        for scene_id, crc in records:
            self._check_scene(before, scene_id, crc)
        payload = bytes([len(records)]) + b"".join(struct.pack("<II", *record) for record in records)
        self.transport.request(COMMANDS["reorder"], payload)
        after = self.snapshot()
        if [s.id for s in after.scenes] != [i for i, crc in records]:
            raise ValueError("Заколка не подтвердила новый порядок.")
        log.info("Scene reorder ACK verified port=%s ids=%s", before.port, [i for i, crc in records])
        return "reorder", 0, before, after

    @staticmethod
    def _check_scene(snapshot, scene_id, checksum):
        scene = next((s for s in snapshot.scenes if s.id == scene_id), None)
        if scene is None or scene.checksum != checksum:
            raise ValueError("Сцена изменилась. Выбери её заново.")
        return scene

    def read_scene(self, scene, caps, progress=lambda done, total: None, cancelled=lambda: False):
        if not caps.supports_scene_read:
            raise ValueError("Для preview сохранённых сцен нужна прошивка с чтением изображений (1.1).")
        total = 24 + scene.width * scene.height * 2
        if scene.type == 2:
            if not caps.supports_animation:
                raise ValueError("Для анимаций нужна firmware 1.6.")
            record = self.transport.request(COMMANDS["asset_info"], struct.pack("<II", scene.id, scene.checksum), timeout=10)
            if len(record) != 4:
                raise ValueError("Неверный размер animation asset.")
            total = struct.unpack("<I", record)[0]
            if not 24 < total <= 512 * 1024:
                raise ValueError("Неверный размер animation asset.")
        if total > caps.max_asset_size:
            raise ValueError("Сцена превышает допустимый размер.")
        data = bytearray()
        while len(data) < total:
            if cancelled():
                raise InterruptedError("Чтение preview отменено.")
            offset = len(data)
            count = min(caps.max_upload_chunk, total - offset)
            reply = self.transport.request(COMMANDS["read_scene"],
                struct.pack("<IIIH", scene.id, scene.checksum, offset, count), timeout=10 if offset == 0 else 5)
            if len(reply) != 4 + count or struct.unpack_from("<I", reply)[0] != offset:
                raise ValueError("Неверный фрагмент preview от заколки.")
            data.extend(reply[4:])
            progress(len(data), total)
        info = parse_asset(data)
        if (info["width"], info["height"], info["payload_crc"], info.get("type", 1)) != (scene.width, scene.height, scene.checksum, scene.type):
            raise ValueError("Сцена изменилась во время чтения. Выбери её заново.")
        return bytes(data)

    def change_scene(self, action, scene_id, expected_checksum):
        if action not in ("activate", "delete"):
            raise ValueError("Неизвестное действие со сценой.")
        before = self.snapshot()
        scene = next((s for s in before.scenes if s.id == scene_id), None)
        if scene is None or scene.checksum != expected_checksum:
            raise ValueError("Сцена уже изменилась или удалена. Выбери её заново.")
        self.transport.request(COMMANDS[action], struct.pack("<I", scene_id), timeout=10)
        after = self.snapshot()
        if action == "activate" and not any(s.id == scene_id and s.active for s in after.scenes):
            raise ValueError("Заколка не подтвердила включение сцены.")
        if action == "delete" and any(s.id == scene_id for s in after.scenes):
            raise ValueError("Заколка не подтвердила удаление сцены.")
        log.info("Scene %s ACK verified port=%s id=%s storage_before=%s storage_after=%s",
                 action, before.port, scene_id, before.capabilities.storage_free, after.capabilities.storage_free)
        return action, scene_id, before, after

    def upload(self, data, progress=lambda done, total: None, cancelled=lambda: False, scene_id=None, expected_checksum=None):
        info = parse_asset(data)
        before = self.snapshot()
        caps = before.capabilities
        if info.get("type") == 2 and not caps.supports_animation:
            raise ValueError("Для загрузки GIF нужна firmware 1.6.")
        if expected_checksum is not None:
            self._check_scene(before, scene_id, expected_checksum)
        if info["width"] > caps.display_width or info["height"] > caps.display_height:
            raise ValueError("Размер сцены больше экрана подключённой заколки.")
        if info.get("type") == 2 and (info["x"] + info["width"] > caps.display_width or info["y"] + info["height"] > caps.display_height):
            raise ValueError("Положение анимации не помещается на этом экране.")
        estimate = estimate_storage(len(data), caps)
        if not estimate.fits:
            raise ValueError(estimate.reason)
        scene_id = next_scene_id(before.scenes) if scene_id is None else scene_id
        if scene_id is None:
            raise ValueError("Все 8 мест заняты. Удали ненужную сцену из списка.")
        if not 1 <= scene_id <= 8:
            raise ValueError("Scene ID must be 1–8")
        log.info("Upload start port=%s scene=%s bytes=%s storage_before=%s", before.port,
                 scene_id, len(data), caps.storage_free)
        command = "upload_begin_typed" if caps.supports_animation else "upload_begin"
        self.transport.request(COMMANDS[command], struct.pack("<III", scene_id, len(data), zlib.crc32(data)))
        for offset in range(0, len(data), caps.max_upload_chunk):
            if cancelled():
                raise InterruptedError("Загрузка остановлена. Предыдущая сцена сохранена.")
            block = data[offset:offset + caps.max_upload_chunk]
            self.transport.request(COMMANDS["upload_chunk"], struct.pack("<I", offset) + block)
            progress(offset + len(block), len(data))
        self.transport.request(COMMANDS["upload_end"], timeout=20)
        after = self.snapshot()
        scene = next((s for s in after.scenes if s.id == scene_id), None)
        if scene is None or scene.type != info.get("type", 1) or scene.checksum != info["payload_crc"] or (scene.width, scene.height) != (info["width"], info["height"]):
            raise ValueError("Заколка не подтвердила сохранённую сцену.")
        if not scene.active:
            self.transport.request(COMMANDS["activate"], struct.pack("<I", scene_id))
            after = self.snapshot()
            if not any(s.id == scene_id and s.active for s in after.scenes):
                raise ValueError("Не удалось включить новую сцену.")
        log.info("Upload ACK verified scene=%s crc=%08x storage_after=%s", scene_id,
                 info["payload_crc"], after.capabilities.storage_free)
        return scene_id, before, after
