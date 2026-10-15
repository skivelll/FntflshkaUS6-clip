from PIL import Image, ImageChops


def decode_rgb565(payload, size):
    if len(payload) != size[0] * size[1] * 2:
        raise ValueError("Invalid RGB565 byte count")
    low, high = Image.frombytes("LA", size, bytes(payload)).split()
    red = high.point(lambda v: (v & 0xF8) | ((v >> 5) & 7))
    green6 = ImageChops.add(high.point(lambda v: (v & 7) << 3), low.point(lambda v: v >> 5))
    green = green6.point(lambda v: (v << 2) | (v >> 4) if v < 64 else 255)
    blue = low.point(lambda v: ((v & 31) << 3) | ((v & 31) >> 2))
    return Image.merge("RGB", (red, green, blue))


def asset_size(width, height):
    return 24 + width * height * 2
