import unittest
from PIL import Image
from fntflshka_us6_clip.core.asset import rgb888_to_rgb565, parse_asset, rgb565_payload
import struct
from fntflshka_us6_clip.media.rgb565 import decode_rgb565, asset_size
from fntflshka_us6_clip.media.image import Placement, prepare_image
from fntflshka_us6_clip.device.models import mock_capabilities
from fntflshka_us6_clip.scenes.models import estimate_storage
from dataclasses import replace


class MediaTests(unittest.TestCase):
    def test_rgb565_known_colors(self):
        self.assertEqual(rgb888_to_rgb565(255, 0, 0), 0xF800)
        self.assertEqual(rgb888_to_rgb565(0, 255, 0), 0x07E0)
        self.assertEqual(rgb888_to_rgb565(0, 0, 255), 0x001F)

    def test_rgb565_roundtrip_matches_wire_pixels(self):
        preview = decode_rgb565(b"\x00\xf8\xe0\x07\x1f\x00\xff\xff", (4, 1))
        self.assertEqual(list(preview.get_flattened_data()), [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)])

    def test_native_codec_matches_all_65536_rgb565_colors(self):
        payload = b"".join(struct.pack("<H", value) for value in range(65536))
        decoded = decode_rgb565(payload, (256, 256))
        expected = bytearray()
        for value in range(65536):
            r, g, b = value >> 11, (value >> 5) & 63, value & 31
            expected.extend(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
        self.assertEqual(decoded.tobytes(), bytes(expected))
        self.assertEqual(rgb565_payload(decoded), payload)

    def test_native_encoder_matches_scalar_at_all_channel_boundaries(self):
        pixels = [(x, y, (x + y) & 255) for y in range(256) for x in range(256)]
        source = Image.new("RGB", (256, 256))
        source.putdata(pixels)
        expected = b"".join(struct.pack("<H", rgb888_to_rgb565(*pixel)) for pixel in pixels)
        self.assertEqual(rgb565_payload(source), expected)

    def test_fit_keeps_entire_image_and_background(self):
        prepared = prepare_image(Image.new("RGBA", (2, 2), "red"), Placement(width=8, height=4, pixel_art=True))
        self.assertEqual(prepared.preview.getpixel((0, 2)), (0, 0, 0))
        self.assertEqual(prepared.preview.getpixel((3, 2)), (255, 0, 0))
        self.assertEqual(prepared.preview.size, (8, 4))

    def test_fill_crops_and_offsets_change_crop(self):
        source = Image.new("RGBA", (8, 2), "blue")
        for x in range(4):
            for y in range(2):
                source.putpixel((x, y), (255, 0, 0, 255))
        left = prepare_image(source, Placement(width=4, height=4, mode="fill", x=4, pixel_art=True))
        right = prepare_image(source, Placement(width=4, height=4, mode="fill", x=-4, pixel_art=True))
        self.assertEqual(left.preview.getpixel((2, 2)), (255, 0, 0))
        self.assertEqual(right.preview.getpixel((2, 2)), (0, 0, 255))

    def test_alpha_composites_on_selected_background(self):
        result = prepare_image(Image.new("RGBA", (1, 1), (255, 0, 0, 0)), Placement(width=2, height=2, background="#00ff00"))
        self.assertEqual(result.preview.getpixel((1, 1)), (0, 255, 0))

    def test_pixel_art_never_adds_intermediate_colors(self):
        source = Image.new("RGBA", (2, 1), "red")
        source.putpixel((1, 0), (0, 0, 255, 255))
        result = prepare_image(source, Placement(width=9, height=5, mode="fill", pixel_art=True))
        self.assertEqual(set(result.preview.get_flattened_data()), {(255, 0, 0), (0, 0, 255)})
        smooth = prepare_image(source, Placement(width=9, height=5, mode="fill"))
        self.assertGreater(len(set(smooth.preview.get_flattened_data())), 2)

    def test_exact_size_includes_v1_header(self):
        result = prepare_image(Image.new("RGBA", (3, 2), "red"), Placement())
        self.assertEqual(len(result.asset), asset_size(320, 172))
        self.assertEqual(len(result.asset), 110104)
        self.assertEqual(parse_asset(result.asset)["width"], 320)

    def test_storage_accounts_for_firmware_reserve_and_limit(self):
        caps = mock_capabilities()
        self.assertTrue(estimate_storage(110104, caps).fits)
        self.assertEqual(estimate_storage(110104, caps).after_payload_bytes, caps.storage_free - 110104)
        self.assertFalse(estimate_storage(110104, replace(caps, storage_free=110104 + 8191)).fits)
        self.assertTrue(estimate_storage(110104, replace(caps, storage_free=110104 + 8192)).fits)
        self.assertFalse(estimate_storage(110105, caps).fits)

    def test_actual_smaller_display_target(self):
        result = prepare_image(Image.new("RGBA", (400, 300), "red"), Placement(width=160, height=86))
        self.assertEqual(result.preview.size, (160, 86))
        self.assertEqual(len(result.asset), asset_size(160, 86))
