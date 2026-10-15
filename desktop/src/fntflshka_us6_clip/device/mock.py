"""Protocol-level mock, injected in place of serial transport. No USB access."""
from dataclasses import replace
import struct
import time
import zlib
from .models import mock_capabilities
from ..core.asset import parse_asset
from ..core.protocol import FIRMWARE_NAME, DeviceNack
from ..scenes.models import Scene


class MockTransport:
    port = "ДЕМО"
    firmware_version = (1, 0)

    def __init__(self, free=None, disconnect_after=None):
        self.caps = mock_capabilities()
        self.caps = replace(self.caps, reserved=3)
        if free is not None:
            self.caps = replace(self.caps, storage_free=free)
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
        if command == 1:
            return FIRMWARE_NAME + b"\x01\x00"
        if command == 2:
            return struct.pack("<BBHHBBIIIIIHH", *self.caps.__dict__.values())
        if command == 3:
            return struct.pack("<II", self.caps.storage_total, self.caps.storage_free)
        if command == 4:
            output = bytearray([len(self.scenes)])
            for s in self.scenes:
                name = s.filename.encode("ascii")
                output.extend(struct.pack("<IIBHHIBB", s.id, s.order, s.type, s.width, s.height,
                                          s.checksum, s.active, len(name)) + name)
            return bytes(output)
        if command == 5:
            scene_id, size, crc = struct.unpack("<III", payload)
            if size + 8192 > self.caps.storage_free:
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
            self.slot_files[filename] = bytes(data)
            self.scenes = [replace(s, active=False) for s in self.scenes if s.id != scene_id]
            self.scenes.append(Scene(scene_id, old.order if old else len(self.scenes), 1, info["width"], info["height"],
                                     info["payload_crc"], True, filename, old.title if old else ""))
            self.scenes.sort(key=lambda s: (s.order, s.id))
            self.file_sizes[scene_id] = size
            self.assets[scene_id] = bytes(data)
            self.caps = replace(self.caps, storage_free=self.caps.storage_free + reclaimed - size)
            self.upload = None
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
                reclaimed += len(self.slot_files.pop(name, b""))
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
