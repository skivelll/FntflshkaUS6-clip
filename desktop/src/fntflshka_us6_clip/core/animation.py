"""Optional asset v1 type 2: bounded independent RGB565 frames."""
import struct
import zlib
from .asset import rgb565_payload

MAX_FILE = 512 * 1024
PREFIX = struct.Struct("<HBBhhII")
ENTRY = struct.Struct("<III")


def minimum_duration(width, height):
    return 170 if width <= 96 and height <= 96 else (250 if width <= 128 and height <= 128 else 350)


def parse_animation(data, width, height, size, crc):
    if len(data) > MAX_FILE or len(data) != 24 + size or size < 16:
        raise ValueError("invalid animation length")
    count, loop, reserved, x, y, frame_bytes, tail = PREFIX.unpack_from(data, 24)
    if (not 2 <= count <= 128 or loop or reserved or tail or frame_bytes != width * height * 2
            or not 0 <= x <= 320 - width or not 0 <= y <= 172 - height
            or size != 16 + count * (12 + frame_bytes)):
        raise ValueError("invalid animation index")
    frames = []
    expected = 16 + count * 12
    for i in range(count):
        offset, duration, checksum = ENTRY.unpack_from(data, 40 + i * 12)
        if offset != expected or not minimum_duration(width, height) <= duration <= 60000:
            raise ValueError("invalid frame offset/duration")
        if zlib.crc32(data[24 + offset:24 + offset + frame_bytes]) != checksum:
            raise ValueError("frame CRC mismatch")
        frames.append((24 + offset, duration, checksum))
        expected += frame_bytes
    return dict(type=2, width=width, height=height, payload_bytes=size, payload_crc=crc,
                x=x, y=y, frame_bytes=frame_bytes, frames=tuple(frames))


def encode_animation(frames, durations, origin=None):
    if not 2 <= len(frames) <= 128 or len(frames) != len(durations):
        raise ValueError("Для анимации нужно 2–128 кадров.")
    width, height = frames[0].size
    if not 1 <= width <= 320 or not 1 <= height <= 172 or any(f.size != (width, height) for f in frames):
        raise ValueError("Неверные размеры кадров.")
    frame_bytes = width * height * 2
    size = 16 + len(frames) * (12 + frame_bytes)
    if size + 24 > MAX_FILE:
        raise ValueError("Анимация больше 512 KiB. Сократи диапазон кадров или выбери компактный размер.")
    x, y = origin or ((320 - width) // 2, (172 - height) // 2)
    if not 0 <= x <= 320 - width or not 0 <= y <= 172 - height:
        raise ValueError("Анимация должна помещаться на LCD.")
    pixels = [rgb565_payload(frame) for frame in frames]
    index = bytearray()
    offset = 16 + len(frames) * 12
    for data, duration in zip(pixels, durations):
        if type(duration) is not int or not minimum_duration(width, height) <= duration <= 60000:
            raise ValueError("Недопустимое время кадра.")
        index.extend(ENTRY.pack(offset, duration, zlib.crc32(data)))
        offset += frame_bytes
    payload = PREFIX.pack(len(frames), 0, 0, x, y, frame_bytes, 0) + index + b"".join(pixels)
    header = struct.pack("<4sBBBBHHII", b"HCAS", 1, 2, 1, 0, width, height, len(payload), zlib.crc32(payload))
    return header + struct.pack("<I", zlib.crc32(header)) + payload
