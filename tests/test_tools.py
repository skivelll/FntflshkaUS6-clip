import unittest
import zlib

from PIL import Image

from tools.make_asset import encode_asset, parse_asset, rgb888_to_rgb565
from tools.clipctl import decode_packet, encode_packet, parse_hello


class ToolTests(unittest.TestCase):
    def test_rgb565(self):
        self.assertEqual(rgb888_to_rgb565(255, 0, 0), 0xF800)
        self.assertEqual(rgb888_to_rgb565(0, 255, 0), 0x07E0)
        self.assertEqual(rgb888_to_rgb565(0, 0, 255), 0x001F)

    def test_asset_header_crc_and_resize(self):
        image = Image.new("RGBA", (640, 344), (255, 0, 0, 255))
        data = encode_asset(image)
        info = parse_asset(data)
        self.assertEqual((info["width"], info["height"]), (320, 172))
        self.assertEqual(info["payload_bytes"], 320 * 172 * 2)
        self.assertEqual(info["payload_crc"], zlib.crc32(data[24:]))
        self.assertEqual(data[24:26], b"\x00\xf8")
        corrupted = bytearray(data)
        corrupted[-1] ^= 1
        with self.assertRaises(ValueError):
            parse_asset(corrupted)

    def test_alpha_compositing(self):
        transparent = Image.new("RGBA", (1, 1), (255, 0, 0, 0))
        self.assertEqual(encode_asset(transparent)[24:26], b"\x00\x00")

    def test_protocol_packet(self):
        packet = encode_packet(10, 0x1234, b"test")
        self.assertEqual(decode_packet(packet), (10, 0x1234, b"test"))
        corrupted = bytearray(packet)
        corrupted[-1] ^= 1
        with self.assertRaises(ValueError):
            decode_packet(corrupted)
        with self.assertRaises(ValueError):
            decode_packet(packet[:-1])

    def test_firmware_identity(self):
        self.assertEqual(parse_hello(b"FntflshkaUS6 clip\x01\x00"), (1, 0))
        with self.assertRaises(ValueError):
            parse_hello(b"wrong device\x01\x00")


if __name__ == "__main__":
    unittest.main()
