"""Convert PNG/JPEG to FntflshkaUS6 clip static RGB565 asset v1."""

import argparse
import struct
import zlib
from pathlib import Path

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--background", default="000000", help="RGB hex for transparent pixels")
    args = parser.parse_args()
    try:
        background = tuple(bytes.fromhex(args.background.lstrip("#")))
        if len(background) != 3:
            raise ValueError
    except ValueError as exc:
        raise SystemExit("--background must be six RGB hex digits") from exc
    with Image.open(args.input) as source:
        data = encode_asset(source, background)
    args.output.write_bytes(data)
    info = parse_asset(data)
    print(f"{args.output}: {info['width']}x{info['height']}, {len(data)} bytes")


if __name__ == "__main__":
    main()
