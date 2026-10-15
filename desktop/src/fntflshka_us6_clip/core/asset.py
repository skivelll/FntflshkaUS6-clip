"""Shared asset v1 codec; no Qt dependency."""
import struct
import zlib
from PIL import Image, ImageOps

HEADER = struct.Struct("<4sBBBBHHIII")
MAX_WIDTH = 320
MAX_HEIGHT = 172


def rgb888_to_rgb565(red: int, green: int, blue: int) -> int:
    return ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)


def encode_asset(image: Image.Image, background: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    image = ImageOps.exif_transpose(image)
    image.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.Resampling.LANCZOS)
    rgba = image.convert("RGBA")
    opaque = Image.new("RGBA", rgba.size, (*background, 255))
    opaque.alpha_composite(rgba)
    rgb = opaque.convert("RGB")
    payload = bytearray()
    pixels = rgb.tobytes()
    for offset in range(0, len(pixels), 3):
        payload.extend(struct.pack("<H", rgb888_to_rgb565(*pixels[offset:offset + 3])))
    prefix = struct.pack(
        "<4sBBBBHHII", b"HCAS", 1, 1, 1, 0,
        rgb.width, rgb.height, len(payload), zlib.crc32(payload),
    )
    return prefix + struct.pack("<I", zlib.crc32(prefix)) + payload


def parse_asset(data: bytes) -> dict[str, int]:
    if len(data) < HEADER.size:
        raise ValueError("asset header is incomplete")
    magic, version, kind, pixel_format, reserved, width, height, size, crc, header_crc = HEADER.unpack_from(data)
    if magic != b"HCAS" or version != 1 or kind != 1 or pixel_format != 1 or reserved != 0:
        raise ValueError("unsupported asset header")
    if not 1 <= width <= MAX_WIDTH or not 1 <= height <= MAX_HEIGHT:
        raise ValueError("invalid dimensions")
    if size != width * height * 2 or len(data) != HEADER.size + size:
        raise ValueError("invalid payload size")
    if zlib.crc32(data[:20]) != header_crc or zlib.crc32(data[24:]) != crc:
        raise ValueError("asset checksum mismatch")
    return {"width": width, "height": height, "payload_bytes": size, "payload_crc": crc}
