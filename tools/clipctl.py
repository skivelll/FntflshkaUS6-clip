"""FntflshkaUS6 clip USB Serial CLI for Ubuntu."""

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

MAGIC = b"HCUP"
FIRMWARE_NAME = b"FntflshkaUS6 clip"
VERSION = 1
MAX_PAYLOAD = 520
MAX_CHUNK = 512
ERRORS = {
    1: "BAD_CRC", 2: "BAD_MAGIC", 3: "UNSUPPORTED_VERSION", 4: "NO_SPACE",
    5: "INVALID_ASSET", 6: "INVALID_COMMAND", 7: "WRITE_ERROR",
    8: "BAD_LENGTH", 9: "BAD_STATE", 10: "NOT_FOUND", 11: "STORAGE_ERROR",
}
COMMANDS = {
    "hello": 1, "capabilities": 2, "storage": 3, "list": 4,
    "upload_begin": 5, "upload_chunk": 6, "upload_end": 7,
    "delete": 8, "activate": 9, "ping": 10,
}


def encode_packet(command: int, sequence: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload exceeds protocol limit")
    body = struct.pack("<4sBBHH", MAGIC, VERSION, command, sequence, len(payload)) + payload
    return body + struct.pack("<I", zlib.crc32(body))


def decode_packet(packet: bytes) -> tuple[int, int, bytes]:
    if len(packet) < 14:
        raise ValueError("incomplete packet")
    magic, version, command, sequence, length = struct.unpack_from("<4sBBHH", packet)
    if magic != MAGIC or version != VERSION or length > MAX_PAYLOAD or len(packet) != 14 + length:
        raise ValueError("invalid packet header or length")
    if zlib.crc32(packet[:-4]) != struct.unpack_from("<I", packet, len(packet) - 4)[0]:
        raise ValueError("packet CRC mismatch")
    return command, sequence, packet[10:-4]


def parse_hello(data: bytes) -> tuple[int, int]:
    if len(data) != len(FIRMWARE_NAME) + 2 or not data.startswith(FIRMWARE_NAME):
        raise ValueError("unexpected firmware identity")
    return data[-2], data[-1]


def candidate_ports() -> list[str]:
    preferred = [p.device for p in list_ports.comports() if p.vid == 0x303A and p.pid == 0x1001]
    others = [p.device for p in list_ports.comports() if p.device not in preferred]
    return preferred + others


class ClipDevice:
    def __init__(self, port: str):
        self.port = port
        self.firmware_version: tuple[int, int] | None = None
        self.serial = serial.Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=5)
        self.serial.dtr = False
        self.serial.rts = False
        self.serial.port = port
        self.serial.open()
        self.sequence = 0
        self.input = bytearray()

    def close(self) -> None:
        self.serial.close()

    def read_packet(self, deadline: float) -> tuple[int, int, bytes]:
        while time.monotonic() < deadline:
            chunk = self.serial.read(256)
            if chunk:
                self.input.extend(chunk)
            while True:
                start = self.input.find(MAGIC)
                if start < 0:
                    del self.input[:-3]
                    break
                if start:
                    del self.input[:start]
                if len(self.input) < 10:
                    break
                length = struct.unpack_from("<H", self.input, 8)[0]
                if length > MAX_PAYLOAD:
                    del self.input[0]
                    continue
                total = 14 + length
                if len(self.input) < total:
                    break
                packet = bytes(self.input[:total])
                del self.input[:total]
                try:
                    return decode_packet(packet)
                except ValueError:
                    continue
        raise TimeoutError(f"no valid reply on {self.port}")

    def request(self, command: int, payload: bytes = b"", timeout: float = 5) -> bytes:
        self.sequence = (self.sequence + 1) & 0xFFFF
        self.serial.write(encode_packet(command, self.sequence, payload))
        deadline = time.monotonic() + timeout
        while True:
            reply, sequence, data = self.read_packet(deadline)
            if sequence != self.sequence or reply not in (0x80, 0x81):
                continue
            if not data or data[0] != command:
                raise ValueError("reply command mismatch")
            if reply == 0x81:
                code = data[1] if len(data) > 1 else -1
                raise RuntimeError(f"device NACK: {ERRORS.get(code, f'error {code}')}")
            return data[1:]


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


def list_scenes(device: ClipDevice) -> None:
    data = device.request(COMMANDS["list"])
    if not data:
        raise ValueError("empty LIST_SCENES response")
    count, offset = data[0], 1
    for _ in range(count):
        if len(data) - offset < 19:
            raise ValueError("truncated scene list")
        scene_id, order, kind, width, height, crc, active, name_length = struct.unpack_from(
            "<IIBHHIBB", data, offset,
        )
        offset += 19
        if len(data) - offset < name_length:
            raise ValueError("truncated scene filename")
        name = data[offset:offset + name_length].decode("ascii")
        offset += name_length
        print(f"id={scene_id} order={order} type={kind} {width}x{height} "
              f"crc={crc:08x} {'ACTIVE' if active else ''} {name}")
    if count == 0:
        print("No uploaded scenes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="serial port; default: auto-discover Espressif USB Serial/JTAG")
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
    device = connect(args.port)
    try:
        if args.action == "info":
            data = device.request(COMMANDS["capabilities"])
            if len(data) != 32:
                raise ValueError("invalid capabilities response")
            protocol, asset, width, height, pixel, _, configured, physical, total, free, max_asset, chunk, render = struct.unpack(
                "<BBHHBBIIIIIHH", data,
            )
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
            device.request(COMMANDS["upload_begin"], struct.pack("<III", args.id, len(data), zlib.crc32(data)))
            for offset in range(0, len(data), MAX_CHUNK):
                block = data[offset:offset + MAX_CHUNK]
                device.request(COMMANDS["upload_chunk"], struct.pack("<I", offset) + block)
                print(f"\r{min(offset + len(block), len(data))}/{len(data)} bytes", end="", flush=True)
            device.request(COMMANDS["upload_end"], timeout=20)
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
