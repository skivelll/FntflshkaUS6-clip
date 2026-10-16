from .capacity import LIBRARY_MAX
"""Shared asset v1 codec; no Qt dependency."""
import struct
import zlib
from PIL import Image, ImageOps, ImageChops

HEADER = struct.Struct("<4sBBBBHHIII")
MAX_WIDTH = 320
MAX_HEIGHT = 172


def rgb888_to_rgb565(red: int, green: int, blue: int) -> int:
    return ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)


def rgb565_payload(rgb: Image.Image) -> bytes:
    """Pack the same little-endian RGB565 with Pillow's native channel operations."""
    red, green, blue = rgb.convert("RGB").split()
    high = ImageChops.add(red.point(lambda v: v & 0xF8), green.point(lambda v: v >> 5))
    low = ImageChops.add(green.point(lambda v: ((v >> 2) & 7) << 5), blue.point(lambda v: v >> 3))
    # LA interleaves two unmodified 8-bit bands: low byte, then high byte.
    return Image.merge("LA", (low, high)).tobytes()


def encode_asset(image: Image.Image, background: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    image = ImageOps.exif_transpose(image)
    image.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.Resampling.LANCZOS)
    rgba = image.convert("RGBA")
    opaque = Image.new("RGBA", rgba.size, (*background, 255))
    opaque.alpha_composite(rgba)
    rgb = opaque.convert("RGB")
    payload = rgb565_payload(rgb)
    prefix = struct.pack(
        "<4sBBBBHHII", b"HCAS", 1, 1, 1, 0,
        rgb.width, rgb.height, len(payload), zlib.crc32(payload),
    )
    return prefix + struct.pack("<I", zlib.crc32(prefix)) + payload


def parse_asset(data: bytes) -> dict[str, int]:
    if len(data) > LIBRARY_MAX:
        raise ValueError("asset exceeds 820 KiB")
    if len(data) < HEADER.size:
        raise ValueError("asset header is incomplete")
    magic, version, kind, pixel_format, reserved, width, height, size, crc, header_crc = HEADER.unpack_from(data)
    if magic != b"HCAS" or version != 1 or kind not in (1, 2) or pixel_format != 1 or reserved != 0:
        raise ValueError("unsupported asset header")
    if not 1 <= width <= MAX_WIDTH or not 1 <= height <= MAX_HEIGHT:
        raise ValueError("invalid dimensions")
    if (kind == 1 and size != width * height * 2) or len(data) != HEADER.size + size:
        raise ValueError("invalid payload size")
    if zlib.crc32(data[:20]) != header_crc or zlib.crc32(data[24:]) != crc:
        raise ValueError("asset checksum mismatch")
    if kind == 2:
        from .animation import parse_animation
        return parse_animation(data, width, height, size, crc)
    return {"width": width, "height": height, "payload_bytes": size, "payload_crc": crc}


def first_frame(data):
    info = parse_asset(data)
    if info.get('encoding') == 1:
        from .compression import decoded_frames
        return next(decoded_frames(data,info))[0]
    offset = info["frames"][0][0] if info.get("type") == 2 else 24
    return data[offset:offset + info["width"] * info["height"] * 2]
