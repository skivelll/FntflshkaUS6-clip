"""Shared USB protocol v1 framing and identity."""
import struct
import zlib

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
    "delete": 8, "activate": 9, "ping": 10, "read_scene": 11,
    "scene_details": 12, "rename": 13, "reorder": 14,
    "get_playlist": 15, "set_playlist": 16,
    "get_transition": 17, "set_playlist_effect": 18,
    "bench_active": 19,
    "bench_animation": 29,
    "asset_info": 20, "upload_begin_typed": 21,
    "list_page": 22, "library_policy": 23, "metadata_begin": 24,
    "metadata_chunk": 25, "metadata_commit": 26, "playlist_page": 27,
    "bench_manifest": 28,
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


class DeviceNack(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"device NACK: {ERRORS.get(code, f'error {code}')}")
