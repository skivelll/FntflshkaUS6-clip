"""Shared serial transport; all calls are synchronous, use off UI thread."""
import struct
import time
import serial
from .protocol import MAGIC, MAX_PAYLOAD, encode_packet, decode_packet, DeviceNack

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
            # Wait for one byte, then consume the available burst. A fixed-size
            # read waits for timeout after every short ACK or final read fragment.
            chunk = self.serial.read(min(4096, self.serial.in_waiting) or 1)
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
        self.last_request={'command':command,'sequence':self.sequence,'payload_length':len(payload),'port':self.port}
        self.serial.write(encode_packet(command, self.sequence, payload))
        deadline = time.monotonic() + timeout
        while True:
            try:
                reply, sequence, data = self.read_packet(deadline)
            except TimeoutError as error:
                error.command=command;error.sequence=self.sequence;error.port=self.port
                raise
            if sequence != self.sequence or reply not in (0x80, 0x81):
                continue
            if not data or data[0] != command:
                raise ValueError("reply command mismatch")
            if reply == 0x81:
                code = data[1] if len(data) > 1 else -1
                error=DeviceNack(code)
                error.command=command;error.sequence=self.sequence;error.port=self.port
                raise error
            self.last_ack={'command':command,'sequence':self.sequence,'port':self.port}
            return data[1:]


def reset_serial_lines(device):
    device.serial.rts = True
    device.serial.dtr = device.serial.dtr
    time.sleep(0.2)
    device.serial.rts = False
    device.serial.dtr = device.serial.dtr
    time.sleep(0.2)
