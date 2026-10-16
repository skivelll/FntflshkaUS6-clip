"""Protocol-level mock, injected in place of serial transport. No USB access."""
from dataclasses import replace
import struct
import time
import zlib
from .models import mock_capabilities
from ..core.asset import parse_asset
from ..core.protocol import FIRMWARE_NAME, DeviceNack
from ..scenes.models import Scene
from ..core.capacity import LIBRARY_MAX, MAX_SCENES, MAX_ID, SERVICE_RESERVE, allocated_size, library_fits


class MockTransport:
    port = "ДЕМО"
    firmware_version = (1, 0)

    def __init__(self, free=None, disconnect_after=None, budget=False, compression=False):
        self.caps = mock_capabilities()
        self.caps = replace(self.caps, reserved=47, max_asset_size=512 * 1024)
        from .playlist import Playlist
        self.playlist = Playlist()
        if free is not None:
            self.caps = replace(self.caps, storage_free=free)
        if budget:
            self.caps = replace(self.caps, reserved=self.caps.reserved | 64, max_asset_size=LIBRARY_MAX)
        if compression:
            self.caps = replace(self.caps, reserved=self.caps.reserved | 128)
            self.firmware_version=(1,8)
        self.metadata = None
        self.scenes = []
        self.connected = True
        self.upload = None
        self.disconnect_after = disconnect_after
        self.file_sizes = {}
        self.assets = {}
        self.slot_files = {}

    def close(self):
        self.connected = False

    def request(self, command, payload=b"", timeout=5):
        if not self.connected:
            raise OSError("Mock disconnect")
        if command in (22, 23, 24, 25, 26, 27, 28) and not self.caps.supports_library_budget:
            raise DeviceNack(6)
        if command == 28:
            # Synthetic mock, no measured hardware heap values.
            if payload or self.upload is not None:
                raise DeviceNack(9)
            return struct.pack("<8I", 1, MAX_SCENES, 0, 0, 0, 0, 0, 0)
        def revision():
            body = repr([(s.id, s.order, s.checksum, s.filename, s.title) for s in self.scenes]).encode()
            return zlib.crc32(body)
        if command == 23:
            return struct.pack("<IIIIHHI", LIBRARY_MAX, sum(allocated_size(len(d)) for d in self.assets.values()),
                               SERVICE_RESERVE, 4096, MAX_SCENES, MAX_ID, revision())
        if command == 22:
            offset, = struct.unpack("<H", payload)
            if offset > len(self.scenes): raise DeviceNack(8)
            page = self.scenes[offset:offset + 10]
            output = bytearray(struct.pack("<HHIIB", len(self.scenes), offset + len(page),
                next((s.id for s in self.scenes if s.active), 0), revision(), len(page)))
            for s in page:
                name = s.filename.encode("ascii")
                output.extend(struct.pack("<IIBHHIBB", s.id, s.order, s.type, s.width, s.height,
                    s.checksum, s.active, len(name)) + name + struct.pack("<I", len(self.assets[s.id])))
            return bytes(output)
        if command == 27:
            data = self.playlist.encode_extended()
            offset, = struct.unpack("<H", payload)
            if offset >= len(data): raise DeviceNack(8)
            return struct.pack("<HHI", len(data), offset, zlib.crc32(data)) + data[offset:offset + 500]
        if command == 24:
            self.metadata = None
            if len(payload) not in (7, 13): raise DeviceNack(8)
            kind, count, token = struct.unpack_from("<BHI", payload)
            if kind > 1 or count > MAX_SCENES or len(payload) != (13 if kind else 7): raise DeviceNack(8)
            if self.upload is not None or token != revision() or (not kind and count != len(self.scenes)): raise DeviceNack(9)
            self.metadata = (kind, count, token, payload[7:], [])
            return b""
        if command == 25:
            if self.metadata is None or self.upload is not None: raise DeviceNack(9)
            records = self.metadata[-1]
            if len(payload) < 10 or (len(payload)-2)%8 or struct.unpack_from("<H", payload)[0] != len(records):
                self.metadata = None; raise DeviceNack(8)
            records.extend(struct.unpack_from("<II", payload, i) for i in range(2, len(payload), 8))
            if len(records) > self.metadata[1]: self.metadata = None; raise DeviceNack(8)
            return b""
        if command == 26:
            pending, self.metadata = self.metadata, None
            if pending is None or self.upload is not None: raise DeviceNack(9)
            kind, count, token, config, records = pending
            current = {s.id: s for s in self.scenes}
            if payload or token != revision() or len(records) != count or len({i for i,c in records}) != count:
                raise DeviceNack(9)
            if any(i not in current or current[i].checksum != c for i,c in records): raise DeviceNack(9)
            if kind:
                from .playlist import Playlist
                enabled, effect, interval = struct.unpack("<BBI", config)
                value = Playlist(bool(enabled), interval, tuple(i for i,c in records), effect)
                value.encode_extended(); self.playlist = value
                if value.enabled: self.scenes = [replace(s, active=s.id == value.ids[0]) for s in self.scenes]
            else:
                if set(current) != {i for i,c in records}: raise DeviceNack(9)
                self.scenes = [replace(current[i], order=n) for n,(i,c) in enumerate(records)]
            return b""
        if command in (5, 21, 8, 9):
            self.playlist = replace(self.playlist, enabled=False)
        if command == 15:
            return self.playlist.encode()
        if command == 17:
            return bytes([self.playlist.transition])
        if command in (16, 18):
            from .playlist import Playlist
            try:
                value = Playlist.decode(payload[:42])
                if len(payload) != 42 + len(value.ids) * 4 + (command == 18):
                    raise ValueError("length")
                if command == 18:
                    value = replace(value, transition=payload[-1])
                    value.encode()
            except (ValueError, struct.error) as error:
                raise DeviceNack(8) from error
            if self.upload is not None:
                raise DeviceNack(9)
            for index, scene_id in enumerate(value.ids):
                scene = next((s for s in self.scenes if s.id == scene_id), None)
                if scene is None or scene.checksum != struct.unpack_from("<I", payload, 42 + index * 4)[0]:
                    raise DeviceNack(9)
            self.playlist = value
            if value.enabled:
                self.scenes = [replace(s, active=s.id == value.ids[0]) for s in self.scenes]
            return b""
        if command == 1:
            return FIRMWARE_NAME + b"\x01\x00"
        if command == 2:
            return struct.pack("<BBHHBBIIIIIHH", *tuple(self.caps.__dict__.values())[:13])
        if command == 3:
            return struct.pack("<II", self.caps.storage_total, self.caps.storage_free)
        if command == 4:
            output = bytearray([len(self.scenes)])
            for s in self.scenes:
                name = s.filename.encode("ascii")
                output.extend(struct.pack("<IIBHHIBB", s.id, s.order, s.type, s.width, s.height,
                                          s.checksum, s.active, len(name)) + name)
            return bytes(output)
        if command == 20:
            scene_id, checksum = struct.unpack("<II", payload)
            scene = next((s for s in self.scenes if s.id == scene_id), None)
            if scene is None or scene.checksum != checksum:
                raise DeviceNack(9)
            return struct.pack("<I", len(self.assets[scene_id]))
        if command in (5, 21):
            scene_id, size, crc = struct.unpack("<III", payload)
            if not 1 <= scene_id <= (MAX_ID if self.caps.supports_library_budget else 8) or size > self.caps.max_asset_size:
                raise DeviceNack(5)
            if self.upload is not None: raise DeviceNack(9)
            old = next((s for s in self.scenes if s.id == scene_id), None)
            if command == 5 and old and old.type == 2: raise DeviceNack(9)
            if self.caps.supports_library_budget:
                used = sum(allocated_size(len(d)) for d in self.assets.values())
                if not library_fits(used, len(self.assets.get(scene_id, b"")), size) or (not old and len(self.scenes) >= MAX_SCENES): raise DeviceNack(4)
                if allocated_size(size) + SERVICE_RESERVE > self.caps.storage_free: raise DeviceNack(4)
            elif size + 8192 > self.caps.storage_free:
                raise DeviceNack(4)
            self.upload = (scene_id, size, crc, bytearray())
        elif command == 6:
            if self.upload is None:
                raise DeviceNack(9)
            offset = struct.unpack_from("<I", payload)[0]
            data = self.upload[3]
            if self.disconnect_after is not None and offset >= self.disconnect_after:
                self.connected = False
                raise OSError("Mock disconnect during upload")
            if offset != len(data):
                raise DeviceNack(8)
            data.extend(payload[4:])
            time.sleep(0.002)
        elif command == 7:
            if self.upload is None:
                raise DeviceNack(9)
            scene_id, size, crc, data = self.upload
            if len(data) != size or zlib.crc32(data) != crc:
                raise DeviceNack(1)
            try:
                info = parse_asset(data)
            except ValueError as error:
                raise DeviceNack(5) from error
            old = next((s for s in self.scenes if s.id == scene_id), None)
            slot = "b" if old and old.filename.endswith("a.bin") else "a"
            filename = f"scenes/{scene_id:04d}{slot}.bin"
            reclaimed = len(self.slot_files.get(filename, b""))
            if self.caps.supports_library_budget: reclaimed = allocated_size(reclaimed)
            self.slot_files[filename] = bytes(data)
            self.scenes = [replace(s, active=False) for s in self.scenes if s.id != scene_id]
            self.scenes.append(Scene(scene_id, old.order if old else len(self.scenes), info.get("type", 1), info["width"], info["height"],
                                     info["payload_crc"], True, filename, old.title if old else "",
                                     size if self.caps.supports_library_budget else 0))
            self.scenes.sort(key=lambda s: (s.order, s.id))
            self.file_sizes[scene_id] = size
            self.assets[scene_id] = bytes(data)
            self.caps = replace(self.caps, storage_free=self.caps.storage_free + reclaimed - (allocated_size(size) if self.caps.supports_library_budget else size))
            self.upload = None
            if self.caps.supports_library_budget:
                referenced = {s.filename for s in self.scenes}
                for name in list(self.slot_files):
                    if name not in referenced:
                        freed = allocated_size(len(self.slot_files.pop(name)))
                        self.caps = replace(self.caps, storage_free=self.caps.storage_free + freed)
        elif command == 8:
            scene_id = struct.unpack("<I", payload)[0]
            if self.upload is not None:
                raise DeviceNack(9)
            if not any(s.id == scene_id for s in self.scenes):
                raise DeviceNack(10)
            self.scenes = [s for s in self.scenes if s.id != scene_id]
            self.assets.pop(scene_id, None)
            reclaimed = 0
            for name in (f"scenes/{scene_id:04d}a.bin", f"scenes/{scene_id:04d}b.bin"):
                size = len(self.slot_files.pop(name, b""))
                reclaimed += allocated_size(size) if self.caps.supports_library_budget else size
            self.file_sizes.pop(scene_id, None)
            self.caps = replace(self.caps, storage_free=self.caps.storage_free + reclaimed)
        elif command == 9:
            scene_id = struct.unpack("<I", payload)[0]
            if self.upload is not None:
                raise DeviceNack(9)
            if not any(s.id == scene_id for s in self.scenes):
                raise DeviceNack(10)
            self.scenes = [replace(s, active=s.id == scene_id) for s in self.scenes]
        elif command == 11:
            scene_id, checksum, offset, count = struct.unpack("<IIIH", payload)
            scene = next((s for s in self.scenes if s.id == scene_id), None)
            if scene is None:
                raise DeviceNack(10)
            if self.upload is not None or scene.checksum != checksum:
                raise DeviceNack(9)
            data = self.assets[scene_id]
            if not 1 <= count <= 512 or offset >= len(data):
                raise DeviceNack(8)
            return struct.pack("<I", offset) + data[offset:offset + count]
        elif command in (12, 13):
            scene_id, checksum = struct.unpack_from("<II", payload)
            scene = next((s for s in self.scenes if s.id == scene_id), None)
            if scene is None:
                raise DeviceNack(10)
            if scene.checksum != checksum or (command == 13 and self.upload is not None):
                raise DeviceNack(9)
            if command == 12:
                title = scene.title.encode("utf-8")
                return bytes([len(title)]) + title
            try:
                title = payload[8:].decode("utf-8")
            except UnicodeDecodeError as error:
                raise DeviceNack(8) from error
            if len(payload[8:]) > 48 or any(ord(c) < 32 or ord(c) == 127 for c in title):
                raise DeviceNack(8)
            self.scenes = [replace(s, title=title) if s.id == scene_id else s for s in self.scenes]
        elif command == 14:
            records = [struct.unpack_from("<II", payload, 1 + i * 8) for i in range(payload[0])]
            if self.upload is not None or set(records) != {(s.id, s.checksum) for s in self.scenes} or len(records) != len(self.scenes):
                raise DeviceNack(9)
            current = {s.id: s for s in self.scenes}
            self.scenes = [replace(current[scene_id], order=index) for index, (scene_id, crc) in enumerate(records)]
        elif command == 10:
            return payload
        else:
            raise DeviceNack(6)
        return b""
