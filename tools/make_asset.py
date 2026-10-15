"""Convert PNG/JPEG to FntflshkaUS6 clip static RGB565 asset v1."""

import argparse
import struct
import zlib
from pathlib import Path

# Keep direct CLI execution usable without installing the desktop package.
sys_path = str(Path(__file__).resolve().parents[1] / "desktop" / "src")
import sys
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)
from fntflshka_us6_clip.core.asset import (
    HEADER, MAX_WIDTH, MAX_HEIGHT, rgb888_to_rgb565, encode_asset, parse_asset,
)


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
