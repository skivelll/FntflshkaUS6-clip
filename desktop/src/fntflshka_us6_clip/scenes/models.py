from dataclasses import dataclass
import struct


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

    @property
    def label(self):
        return self.title or f"Сцена {self.id}"


def decode_scenes(data: bytes) -> tuple[Scene, ...]:
    if not data or data[0] > 8:
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
        scenes.append(Scene(*record[:-2], bool(record[-2]), data[offset:offset + length].decode("ascii")))
        offset += length
    if offset != len(data) or len({s.id for s in scenes}) != len(scenes):
        raise ValueError("Invalid scene list tail or duplicate IDs")
    return tuple(scenes)


def next_scene_id(scenes):
    used = {s.id for s in scenes}
    return next((i for i in range(1, 9) if i not in used), None)


@dataclass(frozen=True)
class StorageEstimate:
    asset_bytes: int
    free_bytes: int
    after_payload_bytes: int
    fits: bool
    reason: str


def estimate_storage(size, caps):
    if size > caps.max_asset_size:
        reason = "Сцена превышает допустимый размер."
    elif size + 8192 > caps.storage_free:
        reason = "На заколке не хватает памяти."
    else:
        reason = "✓ помещается"
    return StorageEstimate(size, caps.storage_free, max(0, caps.storage_free - size),
                           size <= caps.max_asset_size and size + 8192 <= caps.storage_free, reason)


@dataclass(frozen=True)
class StorageProjection:
    total: int
    free: int
    used: int
    upload: int
    reclaim: int

    @property
    def projected_free(self):
        return max(0, min(self.total, self.free - self.upload + self.reclaim))


def storage_projection(caps, upload=0, scene=None):
    """Logical-file forecast; A/B old files and LittleFS blocks are unknown to v1."""
    used = max(0, caps.storage_total - caps.storage_free)
    reclaim = min(used, 24 + scene.width * scene.height * 2) if scene is not None else 0
    return StorageProjection(caps.storage_total, caps.storage_free, used, max(0, upload), reclaim)
