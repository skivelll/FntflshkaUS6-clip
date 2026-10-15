import struct
from PIL import Image
from ..core.asset import rgb888_to_rgb565


def decode_rgb565(payload, size):
    if len(payload) != size[0] * size[1] * 2:
        raise ValueError("Invalid RGB565 byte count")
    pixels = bytearray()
    for (value,) in struct.iter_unpack("<H", payload):
        red, green, blue = value >> 11, (value >> 5) & 63, value & 31
        pixels.extend(((red << 3) | (red >> 2), (green << 2) | (green >> 4), (blue << 3) | (blue >> 2)))
    return Image.frombytes("RGB", size, bytes(pixels))


def asset_size(width, height):
    return 24 + width * height * 2
