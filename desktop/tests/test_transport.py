import struct
import unittest
from fntflshka_us6_clip.core.transport import ClipDevice
from fntflshka_us6_clip.core.protocol import encode_packet, decode_packet, DeviceNack


class FragmentedSerial:
    def __init__(self, nack=False):
        self.pending = bytearray()
        self.nack = nack

    @property
    def in_waiting(self):
        return min(3, len(self.pending))

    def write(self, packet):
        command, sequence, payload = decode_packet(packet)
        invalid = bytearray(encode_packet(0x80, sequence, bytes([command]) + b"wrong"))
        invalid[-1] ^= 1
        self.pending.extend(b"boot log\r\n" + invalid)
        self.pending.extend(encode_packet(0x80, sequence - 1, bytes([command]) + b"stale"))
        self.pending.extend(encode_packet(0x81 if self.nack else 0x80, sequence,
                                         bytes([command, 4]) if self.nack else bytes([command]) + payload))
        return len(packet)

    def read(self, count):
        data = bytes(self.pending[:count])
        del self.pending[:count]
        return data


class TransportTests(unittest.TestCase):
    def device(self, nack=False):
        device = ClipDevice.__new__(ClipDevice)
        device.serial = FragmentedSerial(nack)
        device.port = "mock"
        device.input = bytearray()
        device.sequence = 0
        return device

    def test_fragmented_reply_logs_bad_crc_and_stale_sequence(self):
        self.assertEqual(self.device().request(10, b"hello"), b"hello")

    def test_fragmented_nack_preserves_error_code(self):
        with self.assertRaises(DeviceNack) as error:
            self.device(True).request(5)
        self.assertEqual(error.exception.code, 4)
