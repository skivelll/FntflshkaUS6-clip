"""FntflshkaUS6 clip USB Serial CLI for Windows and Linux."""

import argparse
import struct
import sys
import time
import zlib
from pathlib import Path

import serial
from serial.tools import list_ports

if __package__:
    from .make_asset import parse_asset
else:
    from make_asset import parse_asset

from fntflshka_us6_clip.core.protocol import (
    MAGIC, FIRMWARE_NAME, VERSION, MAX_PAYLOAD, MAX_CHUNK, ERRORS, COMMANDS,
    encode_packet, decode_packet, parse_hello,
)
from fntflshka_us6_clip.core.transport import ClipDevice, reset_serial_lines
from fntflshka_us6_clip.scenes.models import decode_scenes
from fntflshka_us6_clip.device.client import DeviceClient
from fntflshka_us6_clip.device.models import Capabilities


def candidate_ports() -> list[str]:
    ports = list(list_ports.comports())
    preferred = [p.device for p in ports if p.vid == 0x303A and p.pid == 0x1001]
    others = [p.device for p in ports if p.device not in preferred]
    return preferred + others


def connect(port: str | None) -> ClipDevice:
    ports = [port] if port else candidate_ports()
    for name in ports:
        for _ in range(3):
            try:
                device = ClipDevice(name)
                recognized = False
                try:
                    hello = device.request(COMMANDS["hello"], timeout=2)
                    try:
                        device.firmware_version = parse_hello(hello)
                    except ValueError:
                        pass
                    else:
                        recognized = True
                        return device
                finally:
                    if not recognized:
                        device.close()
            except (serial.SerialException, OSError, TimeoutError, ValueError, RuntimeError):
                pass
            time.sleep(0.4)
    raise SystemExit("FntflshkaUS6 clip firmware not found; use --port if needed")


def reset_usb_transport(port: str) -> None:
    """Explicit esptool-style USB Serial/JTAG reset; never used by discovery."""
    device = ClipDevice(port)
    try:
        reset_serial_lines(device)
    finally:
        device.close()
    time.sleep(1.5)  # Allow display, storage and USB service to initialize.


def list_scenes(device: ClipDevice) -> None:
    scenes = decode_scenes(device.request(COMMANDS["list"]))
    for scene in scenes:
        print(f"id={scene.id} order={scene.order} type={scene.type} {scene.width}x{scene.height} "
              f"crc={scene.checksum:08x} {'ACTIVE' if scene.active else ''} {scene.filename}")
    if not scenes:
        print("No uploaded scenes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="serial port; default: auto-discover Espressif USB Serial/JTAG")
    parser.add_argument("--reset", action="store_true",
                        help="explicit USB Serial/JTAG reset before HELLO; requires --port")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("info")
    sub.add_parser("list")
    sub.add_parser("storage")
    sub.add_parser("ping")
    upload = sub.add_parser("upload")
    upload.add_argument("file", type=Path)
    upload.add_argument("--id", type=int, default=1)
    for action in ("delete", "activate"):
        sub.add_parser(action).add_argument("id", type=int)
    args = parser.parse_args()
    if args.reset:
        if not args.port:
            parser.error("--reset requires --port for the known ESP32 USB Serial/JTAG device")
        reset_usb_transport(args.port)
    device = connect(args.port)
    try:
        if args.action == "info":
            data = device.request(COMMANDS["capabilities"])
            caps = Capabilities.decode(data)
            protocol, asset, width, height, pixel, _, configured, physical, total, free, max_asset, chunk, render = caps.__dict__.values()
            major, minor = device.firmware_version or (0, 0)
            print(f"port={device.port} firmware={FIRMWARE_NAME.decode()} {major}.{minor} "
                  f"protocol={protocol} asset={asset}")
            print(f"display={width}x{height} pixel_format={pixel} flash_configured={configured} "
                  f"physical_flash={physical if physical else 'unknown'}")
            print(f"storage_total={total} storage_free={free} max_asset={max_asset} "
                  f"max_chunk={chunk} render_buffer={render}")
        elif args.action == "storage":
            total, free = struct.unpack("<II", device.request(COMMANDS["storage"]))
            print(f"storage_total={total} storage_free={free}")
        elif args.action == "list":
            list_scenes(device)
        elif args.action == "ping":
            print(device.request(COMMANDS["ping"], b"FntflshkaUS6 clip").decode())
        elif args.action == "upload":
            data = args.file.read_bytes()
            info = parse_asset(data)
            def progress(done, total):
                print(f"\r{done}/{total} bytes", end="", flush=True)
            DeviceClient(device).upload(data, progress, scene_id=args.id)
            print(f"\nUploaded scene {args.id}: {info['width']}x{info['height']}")
            list_scenes(device)
        elif args.action in ("delete", "activate"):
            device.request(COMMANDS[args.action], struct.pack("<I", args.id), timeout=10)
            print(f"{args.action} scene {args.id}: ACK")
    finally:
        device.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TimeoutError, RuntimeError, serial.SerialException) as exc:
        print(f"clipctl: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
