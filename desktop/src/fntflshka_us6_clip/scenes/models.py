from dataclasses import dataclass
import struct
from ..core.capacity import MAX_SCENES, MAX_ID, allocated_size


@dataclass(frozen=True)
class Scene:
    id: int
    order: int
    type: int
    width: int
    height: int
    checksum: int
    active: bool
    filename: str
    title: str = ""
    size: int = 0

    @property
    def label(self):
        return self.title or f"Сцена {self.id}"


def decode_scenes(data: bytes, extended=False) -> tuple[Scene, ...]:
    if not data or data[0] > (MAX_SCENES if extended else 8):
        raise ValueError("Invalid scene list")
    offset, scenes = 1, []
    for _ in range(data[0]):
        if len(data) - offset < 19:
            raise ValueError("Truncated scene record")
        record = struct.unpack_from("<IIBHHIBB", data, offset)
        offset += 19
        length = record[-1]
        if len(data) - offset < length:
            raise ValueError("Truncated filename")
        scene = Scene(*record[:-2], bool(record[-2]), data[offset:offset + length].decode("ascii"))
        offset += length
        if extended:
            from dataclasses import replace
            if len(data) - offset < 4:
                raise ValueError("Truncated scene size")
            scene = replace(scene, size=struct.unpack_from("<I", data, offset)[0])
            offset += 4
        if not 1 <= scene.id <= MAX_ID or scene.type not in (1, 2) or record[-2] > 1:
            raise ValueError("Invalid scene identity")
        scenes.append(scene)
    if offset != len(data) or len({s.id for s in scenes}) != len(scenes):
        raise ValueError("Invalid scene list tail or duplicate IDs")
    return tuple(scenes)


def next_scene_id(scenes, caps=None):
    used = {s.id for s in scenes}
    limit = caps.id_limit if caps else 8
    if len(used) >= (caps.scene_limit if caps else 8):
        return None
    return next((i for i in range(1, limit + 1) if i not in used), None)


@dataclass(frozen=True)
class StorageEstimate:
    asset_bytes: int
    free_bytes: int
    after_payload_bytes: int
    fits: bool
    reason: str


def estimate_storage(size, caps, old_size=0):
    required = allocated_size(size) if caps.supports_library_budget else size
    projected = caps.library_used - allocated_size(old_size) + allocated_size(size)
    if size > caps.max_asset_size:
        reason = "Сцена превышает допустимый размер."
    elif caps.supports_library_budget and projected > caps.library_limit and not (caps.library_used > caps.library_limit and projected < caps.library_used):
        reason = f"Библиотека заполнена: предел {caps.library_limit // 1024} KiB. Удали сцену или уменьши файл."
    elif required + caps.service_reserve > caps.storage_free:
        reason = "На заколке не хватает памяти."
    else:
        reason = "✓ помещается"
    return StorageEstimate(size, caps.storage_free, max(0, caps.storage_free - size),
                           size <= caps.max_asset_size and required + caps.service_reserve <= caps.storage_free
                           and (not caps.supports_library_budget or projected <= caps.library_limit
                                or (caps.library_used > caps.library_limit and projected < caps.library_used)), reason)


@dataclass(frozen=True)
class StorageProjection:
    total: int
    free: int
    used: int
    upload: int
    reclaim: int
    library: bool = False
    reserve: int = 8192
    physical_free: int = 0

    @property
    def projected_free(self):
        if self.library:
            return max(0, min(self.total, self.total - self.used - self.upload + self.reclaim))
        return max(0, min(self.total, self.free - self.upload + self.reclaim))


def storage_projection(caps, upload=0, scene=None, asset_size=None, old_size=0):
    """Logical-file forecast; A/B old files and LittleFS blocks are unknown to v1."""
    used = max(0, caps.storage_total - caps.storage_free)
    logical = asset_size if asset_size is not None else (24 + scene.width * scene.height * 2 if scene and scene.type == 1 else 0)
    if caps.supports_library_budget:
        reclaim = allocated_size(logical) if scene else allocated_size(old_size)
        return StorageProjection(caps.library_limit, max(0, caps.library_limit - caps.library_used),
                                 caps.library_used, allocated_size(upload), reclaim, True,
                                 caps.service_reserve, caps.storage_free)
    reclaim = min(used, logical) if scene is not None else 0
    return StorageProjection(caps.storage_total, caps.storage_free, used, max(0, upload), reclaim)
