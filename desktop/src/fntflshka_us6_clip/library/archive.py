from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import zipfile
from ..core.asset import parse_asset

FORMAT = "fntflshka-us6-clip-collection"
MAX_ARCHIVE = 8 * 512 * 1024 + 65536
MAX_MANIFEST = 16384
MAX_ASSET = 512 * 1024


@dataclass(frozen=True)
class CollectionScene:
    id: int
    title: str
    asset: bytes


@dataclass(frozen=True)
class Collection:
    title: str
    created_utc: str
    display: tuple[int, int]
    active_id: int | None
    scenes: tuple[CollectionScene, ...]  # Tuple order is the collection order.


def validate(collection):
    if not isinstance(collection.title, str) or not 1 <= len(collection.title.strip()) <= 120:
        raise ValueError("Название коллекции должно содержать 1–120 символов.")
    if not isinstance(collection.created_utc, str) or len(collection.created_utc) > 64:
        raise ValueError("Неверная дата коллекции.")
    if (len(collection.display) != 2 or any(type(v) is not int for v in collection.display)
            or not 1 <= collection.display[0] <= 320 or not 1 <= collection.display[1] <= 172):
        raise ValueError("Неверный экран коллекции.")
    if not 1 <= len(collection.scenes) <= 8:
        raise ValueError("В коллекции должно быть 1–8 сцен.")
    ids = set()
    for scene in collection.scenes:
        if type(scene.id) is not int or not 1 <= scene.id <= 8 or scene.id in ids:
            raise ValueError("Неверные или повторные ID сцен в архиве.")
        ids.add(scene.id)
        if not isinstance(scene.title, str) or len(scene.title.encode("utf-8")) > 48 or any(ord(c) < 32 or ord(c) == 127 for c in scene.title):
            raise ValueError("Неверное название сцены в архиве.")
        info = parse_asset(scene.asset)
        if info["width"] > collection.display[0] or info["height"] > collection.display[1]:
            raise ValueError("Сцена больше экрана коллекции.")
    if collection.active_id is not None and (type(collection.active_id) is not int or collection.active_id not in ids):
        raise ValueError("Активная сцена отсутствует в архиве.")
    return collection


def from_snapshot(snapshot, assets, title, selected_id=None):
    scenes = []
    for scene in snapshot.scenes:
        if selected_id is not None and scene.id != selected_id:
            continue
        key = (snapshot.port, scene.id, scene.checksum, scene.width, scene.height)
        data = assets.get(key)
        if data is None:
            raise ValueError("Не все сцены загружены в RAM. Дождись завершения загрузки.")
        info = parse_asset(data)
        if (info["width"], info["height"], info["payload_crc"], info.get("type", 1)) != (scene.width, scene.height, scene.checksum, scene.type):
            raise ValueError("Кэш сцены устарел. Обнови подключение.")
        scenes.append(CollectionScene(scene.id, scene.title, data))
    active = next((s.id for s in snapshot.scenes if s.active and any(item.id == s.id for item in scenes)), None)
    return validate(Collection(title.strip(), datetime.now(timezone.utc).isoformat(),
                               (snapshot.capabilities.display_width, snapshot.capabilities.display_height), active, tuple(scenes)))


def encode(collection):
    validate(collection)
    records = [{"id": s.id, "order": i, "title": s.title, "asset": f"assets/{s.id:04}.bin",
                "sha256": hashlib.sha256(s.asset).hexdigest()} for i, s in enumerate(collection.scenes)]
    manifest = {"format": FORMAT, "version": 1, "title": collection.title,
                "created_utc": collection.created_utc, "display": list(collection.display),
                "active_id": collection.active_id, "scenes": records}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("collection.json", json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
        for scene in collection.scenes:
            archive.writestr(f"assets/{scene.id:04}.bin", scene.asset)
    return buffer.getvalue()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Повторные JSON-поля в архиве.")
        result[key] = value
    return result


def decode(data):
    if len(data) > MAX_ARCHIVE:
        raise ValueError("Архив слишком большой.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            if not 2 <= len(infos) <= 9 or len(set(names)) != len(names) or "collection.json" not in names:
                raise ValueError("Неверный состав архива.")
            for info in infos:
                limit = MAX_MANIFEST if info.filename == "collection.json" else MAX_ASSET
                if (info.file_size > limit or info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16)
                        or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise ValueError("Неподдерживаемый или слишком большой файл в архиве.")
            manifest = json.loads(archive.read("collection.json").decode("utf-8"), object_pairs_hook=unique_object)
            if manifest["format"] != FORMAT or type(manifest["version"]) is not int or manifest["version"] != 1:
                raise ValueError("Неподдерживаемая версия desktop-коллекции.")
            records = manifest["scenes"]
            if not isinstance(records, list) or not 1 <= len(records) <= 8:
                raise ValueError("Неверный список сцен архива.")
            scenes, expected = [], {"collection.json"}
            for index, record in enumerate(records):
                scene_id = record["id"]
                if type(scene_id) is not int or not 1 <= scene_id <= 8 or type(record["order"]) is not int or record["order"] != index:
                    raise ValueError("Неверные ID или порядок архива.")
                path = f"assets/{scene_id:04}.bin"
                if record["asset"] != path:
                    raise ValueError("Неверный путь asset в архиве.")
                asset = archive.read(path)
                if hashlib.sha256(asset).hexdigest() != record["sha256"]:
                    raise ValueError("Контрольная сумма архива не совпала.")
                expected.add(path)
                scenes.append(CollectionScene(scene_id, record["title"], asset))
            if set(names) != expected:
                raise ValueError("В архиве есть посторонние файлы.")
            return validate(Collection(manifest["title"], manifest["created_utc"], tuple(manifest["display"]),
                                       manifest["active_id"], tuple(scenes)))
    except (zipfile.BadZipFile, KeyError, TypeError, UnicodeError, json.JSONDecodeError, RuntimeError, NotImplementedError, RecursionError) as error:
        raise ValueError("Повреждённый или неподдерживаемый архив коллекции.") from error


def load(path):
    with Path(path).open("rb") as file:
        return decode(file.read(MAX_ARCHIVE + 1))


def save(path, collection):
    """Atomic desktop-file replacement; never leave a truncated previous backup."""
    payload = encode(collection)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".clip-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
