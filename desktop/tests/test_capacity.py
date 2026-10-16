import struct
import unittest
from dataclasses import replace
from unittest.mock import patch
from PIL import Image
from fntflshka_us6_clip.core.asset import encode_asset, parse_asset
from fntflshka_us6_clip.core.animation import encode_animation
from fntflshka_us6_clip.core.capacity import LIBRARY_MAX, MAX_SCENES, allocated_size
from fntflshka_us6_clip.core.protocol import COMMANDS, DeviceNack
from fntflshka_us6_clip.device.client import DeviceClient
from fntflshka_us6_clip.device.mock import MockTransport
from fntflshka_us6_clip.device.playlist import Playlist
from fntflshka_us6_clip.library.archive import Collection, CollectionScene, encode, decode
from fntflshka_us6_clip.library.restore import make_plan, execute
from fntflshka_us6_clip.media.animation import preview_frames
from fntflshka_us6_clip.media.animation import prepare_gif, GifSource
from fntflshka_us6_clip.media.image import Placement
from pathlib import Path
from fntflshka_us6_clip.scenes.models import Scene, storage_projection


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.transport = MockTransport(budget=True)
        self.client = DeviceClient(self.transport)
        self.data = encode_asset(Image.new('RGB', (1, 1), 'red'))

    def populate(self, count):
        with patch('fntflshka_us6_clip.device.mock.time.sleep'):
            for i in range(count):
                self.client.upload(self.data)

    def test_more_than_eight_scenes_paginate_rename_reorder_playlist_and_delete(self):
        self.populate(71)
        before = self.client.snapshot()
        self.assertEqual(len(before.scenes), 71)
        self.assertEqual(before.capabilities.library_used, 71 * 4096)
        self.assertTrue(all(s.size == len(self.data) for s in before.scenes))
        self.client.rename_scene(71, before.scenes[-1].checksum, 'Сцена за пределами восьми')
        records = [(s.id, s.checksum) for s in reversed(self.client.snapshot().scenes)]
        self.client.reorder_scenes(records)
        self.assertEqual([s.id for s in self.client.snapshot().scenes], list(range(71, 0, -1)))
        value = Playlist(True, 3000, tuple(range(1, 72)), 3)
        self.client.set_playlist(value, self.client.snapshot())
        self.assertEqual(self.client.get_playlist(), value)
        self.client.change_scene('delete', 71, before.scenes[-1].checksum)
        self.assertEqual(len(self.client.snapshot().scenes), 70)

    def test_budget_allocation_and_safe_replacement_at_full_capacity(self):
        self.populate(MAX_SCENES)
        snapshot = self.client.snapshot()
        self.assertEqual(snapshot.capabilities.library_used, LIBRARY_MAX)
        with self.assertRaises(ValueError):
            self.client.upload(self.data)
        blue = encode_asset(Image.new('RGB', (1, 1), 'blue'))
        self.client.upload(blue, scene_id=1, expected_checksum=snapshot.scenes[0].checksum)
        self.assertEqual(self.client.snapshot().capabilities.library_used, LIBRARY_MAX)
        self.assertEqual(len(self.transport.slot_files), MAX_SCENES)
        self.assertEqual(self.transport.assets[1], blue)
        # Firmware must enforce the budget even when a host skips its preflight.
        with self.assertRaises(DeviceNack) as error:
            self.transport.request(21, struct.pack('<III', 60000, 26, 0))
        self.assertEqual(error.exception.code, 4)

    def test_interrupted_metadata_never_changes_order_or_playlist_and_stale_commit_rejected(self):
        self.populate(12)
        before = self.client.snapshot()
        self.transport.request(24, struct.pack('<BHI', 0, 12, before.capabilities.library_revision))
        records = [(s.id, s.checksum) for s in reversed(before.scenes)]
        self.transport.request(25, struct.pack('<H', 0) + b''.join(struct.pack('<II', *r) for r in records[:5]))
        self.assertEqual(self.client.snapshot().scenes, before.scenes)
        with self.assertRaises(DeviceNack): self.transport.request(26)
        self.assertEqual(self.client.snapshot().scenes, before.scenes)
        self.transport.request(24, struct.pack('<BHI', 0, 12, before.capabilities.library_revision))
        self.transport.request(25, struct.pack('<H', 0) + b''.join(struct.pack('<II', *r) for r in records))
        self.client.rename_scene(1, before.scenes[0].checksum, 'Изменено')
        with self.assertRaises(DeviceNack): self.transport.request(26)
        self.assertEqual([s.id for s in self.client.snapshot().scenes], list(range(1, 13)))

    def test_pagination_mutation_is_rejected(self):
        self.populate(12)
        original = self.transport.request
        def request(command, payload=b'', timeout=5):
            if command == 22 and struct.unpack('<H', payload)[0]:
                self.transport.scenes[0] = replace(self.transport.scenes[0], title='changed')
            return original(command, payload, timeout)
        with patch.object(self.transport, 'request', side_effect=request):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.client.snapshot()

    def test_larger_files_and_over_128_frames_exact_readback_bounded_preview(self):
        full = encode_animation([Image.new('RGB', (320, 172), 'blue')] * 7, [350] * 7)
        self.assertGreater(len(full), 512 * 1024)
        self.client.upload(full)
        snapshot = self.client.snapshot()
        self.assertEqual(self.client.read_scene(snapshot.scenes[0], snapshot.capabilities), full)
        tiny = encode_animation([Image.new('RGB', (1, 1), 'red')] * 1000, [170] * 1000)
        self.assertEqual(len(parse_asset(tiny)['frames']), 1000)
        previews = preview_frames(tiny)
        for i in range(10):
            self.assertEqual(previews[i][0].getpixel((159, 85)), (255, 0, 0))
        self.assertEqual(len(previews.canvases), 2)
        self.assertEqual(previews.frames[0][0].size, (1, 1))

    def test_overbudget_legacy_content_preserved_only_shrinking_allowed(self):
        self.transport.caps = replace(self.transport.caps, reserved=47, max_asset_size=512 * 1024)
        data = encode_asset(Image.new('RGB', (320, 172)))
        with patch('fntflshka_us6_clip.device.mock.time.sleep'):
            for i in range(8): self.client.upload(data)
        saved = dict(self.transport.assets)
        self.transport.caps = replace(self.transport.caps, reserved=111, max_asset_size=LIBRARY_MAX)
        self.assertGreater(self.client.snapshot().capabilities.library_used, LIBRARY_MAX)
        with self.assertRaises(ValueError): self.client.upload(self.data)
        self.assertEqual(self.transport.assets, saved)
        old = self.client.snapshot().scenes[0]
        self.client.upload(self.data, scene_id=old.id, expected_checksum=old.checksum)
        self.assertLessEqual(self.client.snapshot().capabilities.library_used, LIBRARY_MAX)

    def test_extended_collection_and_restore_uses_actual_budget(self):
        collection = Collection('Много сцен', '', (320, 172), 12,
            tuple(CollectionScene(i, f'Рисунок {i}', self.data) for i in range(1, 13)))
        self.assertEqual(decode(encode(collection)), collection)
        plan = make_plan(collection, self.client.snapshot(), ['add'] * 12)
        final = execute(self.client, plan)
        self.assertEqual(len(final.scenes), 12)
        self.assertTrue(next(s for s in final.scenes if s.id == 12).active)
        self.assertEqual(final.capabilities.library_used, 12 * allocated_size(len(self.data)))

    def test_insufficient_staging_space_rejected_before_write(self):
        self.transport.caps = replace(self.transport.caps, storage_free=128 * 1024)
        with self.assertRaises(ValueError): self.client.upload(self.data)
        self.assertIsNone(self.transport.upload)

    def test_long_gif_range_and_large_step_are_not_silently_clamped(self):
        frames = (Image.new('RGB', (1, 1), 'blue'),) * 1000
        result = prepare_gif((frames, (20,) * 1000),
            GifSource(Path('unused.gif'), 'full', 500, 1000, 250), Placement(mode='fill'))
        info = parse_asset(result.asset)
        self.assertEqual(len(info['frames']), 2)
        self.assertEqual([duration for offset, duration, crc in info['frames']], [5000, 5000])

    def test_overbudget_delete_forecast_does_not_promise_free_quota_too_early(self):
        caps = replace(self.client.snapshot().capabilities, library_used=LIBRARY_MAX + 16384)
        scene = Scene(1, 0, 1, 1, 1, 0, False, 'scenes/0001a.bin')
        self.assertEqual(storage_projection(caps, scene=scene, asset_size=26).projected_free, 0)
        self.assertEqual(storage_projection(caps, scene=scene, asset_size=20000).projected_free, 4096)
        self.assertEqual(storage_projection(caps, upload=26, old_size=32768).projected_free, 12288)


if __name__ == '__main__':
    unittest.main()
