from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib
from PIL import Image
from fntflshka_us6_clip.core.asset import parse_asset, first_frame, encode_asset
from fntflshka_us6_clip.core.animation import encode_animation
from fntflshka_us6_clip.media.animation import load_gif, prepare_gif, preview_frames, GifSource, prepare_cached
from fntflshka_us6_clip.media.image import Placement, SceneSource
from fntflshka_us6_clip.device.client import DeviceClient
from fntflshka_us6_clip.device.mock import MockTransport
from fntflshka_us6_clip.library.archive import Collection, CollectionScene, encode, decode


def asset(size=(128, 128)):
    return encode_animation([Image.new('RGB', size, c) for c in ('red', 'blue')],
                            [350, 700], (0, 0))


def repair(data):
    data = bytearray(data)
    struct.pack_into('<I', data, 16, zlib.crc32(data[24:]))
    struct.pack_into('<I', data, 20, zlib.crc32(data[:20]))
    return bytes(data)


class AnimationTests(unittest.TestCase):
    def test_profiles_and_exact_first_pixels(self):
        for size in ((96, 96), (128, 128), (320, 172)):
            data = asset(size)
            info = parse_asset(data)
            self.assertEqual(info['type'], 2)
            self.assertEqual(len(info['frames']), 2)
            self.assertEqual(first_frame(data)[:2], b'\x00\xf8')
            frames = preview_frames(data)
            self.assertEqual(frames[0][0].getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(frames[1][0].getpixel((0, 0)), (0, 0, 255))

    def test_malformed_index_duration_origin_and_crc_rejected(self):
        original = asset((16, 16))
        for offset, value in ((24, b'\xff\xff'), (26, b'\x01'), (28, b'\xff\xff'),
                              (40, struct.pack('<I', 1)), (44, struct.pack('<I', 1)),
                              (52, struct.pack('<I', 40))):
            data = bytearray(original); data[offset:offset+len(value)] = value
            with self.assertRaises(ValueError): parse_asset(repair(data))
        data = bytearray(original); data[-1] ^= 1
        with self.assertRaises(ValueError): parse_asset(repair(data))
        for data in (original[:-1], original + b'\0'):
            with self.assertRaises(ValueError): parse_asset(data)

    def test_no_silent_truncation_and_file_cap(self):
        with self.assertRaises(ValueError):
            encode_animation([Image.new('RGB', (320, 172))] * 8, [350] * 8)
        with self.assertRaises(ValueError): encode_animation([Image.new('RGB', (1, 1))], [350])

    def test_static_codec_stays_identical(self):
        data = encode_asset(Image.new('RGB', (2, 2), 'red'))
        self.assertNotIn('type', parse_asset(data))
        self.assertEqual(first_frame(data), data[24:])

    def test_edit_cached_animation_changes_all_frames_preserving_duration(self):
        for size in ((128,128),(320,172)):
            prepared = prepare_cached(SceneSource(asset(size),(320,172)), Placement(x=20))
            info = parse_asset(prepared.asset)
            self.assertEqual([f[1] for f in info['frames']], [350,700])
            self.assertEqual(prepared.frames[0][0].getpixel((0,0)), (0,0,0))
            self.assertEqual(prepared.frames[0][0].getpixel((20,20)), (255,0,0))
            self.assertEqual(prepared.frames[1][0].getpixel((20,20)), (0,0,255))

    def test_device_upload_read_exact_and_archive_roundtrip(self):
        transport = MockTransport()
        client = DeviceClient(transport)
        data = asset()
        after = client.upload(data)[2]
        scene = after.scenes[0]
        self.assertEqual(scene.type, 2)
        self.assertEqual(client.read_scene(scene, after.capabilities), data)
        collection = Collection('GIF', '2026-10-15', (320, 172), 1, (CollectionScene(1, 'GIF', data),))
        self.assertEqual(decode(encode(collection)), collection)

    def test_old_firmware_and_interrupted_replacement_keep_previous(self):
        transport = MockTransport()
        client = DeviceClient(transport)
        data = asset()
        transport.caps = replace(transport.caps, reserved=15)
        with self.assertRaises(ValueError): client.upload(data)
        self.assertEqual(transport.scenes, [])
        transport.caps = replace(transport.caps, reserved=47)
        client.upload(data)
        before = list(transport.scenes)
        transport.disconnect_after = 512
        with self.assertRaises(OSError):
            client.upload(asset((96, 96)), scene_id=1, expected_checksum=before[0].checksum)
        self.assertEqual(transport.scenes, before)
        self.assertEqual(transport.assets[1], data)

    def test_gif_disposal_ranges_profiles_and_timing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'frames.gif'
            frames = [Image.new('RGB', (24, 24), c) for c in ('red', 'blue', 'green')]
            frames[0].save(path, save_all=True, append_images=frames[1:], duration=[100, 200, 300], loop=0, disposal=2)
            loaded = load_gif(path)
            self.assertEqual(loaded[1], (100, 200, 300))
            for mode, size in (('compact', (128,128)), ('full', (320,172))):
                prepared = prepare_gif(loaded, GifSource(path, mode, 0, 2, 1), Placement())
                info = parse_asset(prepared.asset)
                self.assertEqual((info['width'], info['height']), size)
                self.assertEqual(len(info['frames']), 2)
                self.assertEqual(prepared.frames[1][0].getpixel((160,86)), (0,0,255))
            with self.assertRaises(ValueError): prepare_gif(loaded, GifSource(path, 'compact', 2, 1), Placement())

    def test_gif_transparency_and_restore_previous_disposal(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'disposal.gif'
            palette = [0,0,0,255,0,0,0,0,255,0,255,0] + [0] * (768-12)
            frames = [Image.new('P',(16,16),color) for color in (1,0,0)]
            for frame in frames: frame.putpalette(palette)
            from PIL import ImageDraw
            ImageDraw.Draw(frames[1]).rectangle((0,0,7,7),fill=2)
            ImageDraw.Draw(frames[2]).rectangle((8,8,15,15),fill=3)
            frames[0].save(path,save_all=True,append_images=frames[1:],duration=100,loop=0,
                           transparency=0,disposal=[1,3,1],optimize=False)
            decoded, _ = load_gif(path)
            self.assertEqual(decoded[1].getpixel((0,0))[:3],(0,0,255))
            self.assertEqual(decoded[1].getpixel((12,12))[:3],(255,0,0))
            self.assertEqual(decoded[2].getpixel((0,0))[:3],(255,0,0))
            self.assertEqual(decoded[2].getpixel((12,12))[:3],(0,255,0))


class AnimationGuiTests(unittest.TestCase):
    def test_large_source_gif_selected_eight_frames_and_step_timing(self):
        from fntflshka_us6_clip.ui.gif_dialog import gif_frame_count
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'large.gif'
            frames = [Image.new('RGB',(1000,1000),(i*7,0,0)) for i in range(34)]
            frames[0].save(path,save_all=True,append_images=frames[1:],duration=100,optimize=False)
            del frames
            self.assertEqual(gif_frame_count(path),34)
            with self.assertRaisesRegex(ValueError,'32 MP'):
                load_gif(path)
            request = GifSource(path,'compact',0,8,1)
            loaded = load_gif(path,request)
            self.assertEqual(len(loaded[0]),8)
            prepared = prepare_gif(loaded,replace(request,first=0,last=0,step=1),Placement())
            self.assertEqual(len(parse_asset(prepared.asset)['frames']),8)
            request = GifSource(path,'compact',2,10,3)
            loaded = load_gif(path,request)
            self.assertEqual(loaded[1],(300,300,200))
            self.assertEqual([frame.getpixel((0,0))[0] for frame in loaded[0]],[14,35,56])

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_gif_range_buttons_increment_and_decrement(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QPushButton
        from fntflshka_us6_clip.ui.gif_dialog import GifDialog
        dialog = GifDialog(None)
        dialog.show()
        try:
            for name, spin in (("first",dialog.first),("last",dialog.last),("step",dialog.step)):
                before = spin.value()
                QTest.mouseClick(dialog.findChild(QPushButton,f'gif_{name}_up'),Qt.LeftButton)
                self.assertEqual(spin.value(),before+1)
                QTest.mouseClick(dialog.findChild(QPushButton,f'gif_{name}_down'),Qt.LeftButton)
                self.assertEqual(spin.value(),before)
            dialog.first.setValue(501)
            dialog.last.setValue(1000)
            dialog.step.setValue(250)
            self.assertEqual(dialog.options(), ('compact', 500, 1000, 250))
        finally: dialog.close()

    def test_gif_memory_updates_for_range_profile_and_available_space(self):
        from fntflshka_us6_clip.ui.gif_dialog import GifDialog
        caps = DeviceClient(MockTransport(budget=True)).snapshot().capabilities
        caps = replace(caps, library_used=700*1024)
        dialog = GifDialog(None, frame_count=30, caps=caps)
        try:
            self.assertIn("30 из 30", dialog.memory.text())
            self.assertIn("Превышен предел файла", dialog.memory.text())
            self.assertFalse(dialog.prepare_button.isEnabled())
            dialog.last.setValue(4)
            self.assertIn("Не помещается в библиотеку", dialog.memory.text())
            dialog.step.setValue(2)
            self.assertIn("2 из 30", dialog.memory.text())
            self.assertIn("Помещается", dialog.memory.text())
            self.assertTrue(dialog.prepare_button.isEnabled())
            dialog.mode.setCurrentIndex(dialog.mode.findData('full'))
            self.assertFalse(dialog.prepare_button.isEnabled())
            dialog.caps = replace(caps, library_used=0, storage_free=100*1024)
            dialog.update_memory()
            self.assertIn("Для безопасной записи", dialog.memory.text())
            self.assertFalse(dialog.prepare_button.isEnabled())
        finally:
            dialog.close()

    def test_gif_memory_replacement_credit_and_offline(self):
        from fntflshka_us6_clip.ui.gif_dialog import GifDialog, gif_frame_count
        caps = DeviceClient(MockTransport(budget=True)).snapshot().capabilities
        caps = replace(caps, library_used=caps.library_limit)
        dialog = GifDialog(None, frame_count=2, caps=caps, old_size=70000)
        try:
            self.assertTrue(dialog.prepare_button.isEnabled())
            dialog.caps = None
            dialog.update_memory()
            self.assertIn("доступное место неизвестно", dialog.memory.text())
            dialog.first.setValue(2)
            self.assertFalse(dialog.prepare_button.isEnabled())
        finally:
            dialog.close()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'frames.gif'
            Image.new('RGB',(2,2),'red').save(path,save_all=True,append_images=[Image.new('RGB',(2,2),'blue')])
            self.assertEqual(gif_frame_count(path), 2)

    def test_choose_both_profiles_upload_and_cached_animation(self):
        from fntflshka_us6_clip.ui.main_window import MainWindow
        from test_gui_flow import wait_for
        window = MainWindow(mock=True)
        window.show()
        try:
            self.assertTrue(wait_for(lambda: window.snapshot is not None and not window.cache_loading))
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'animated.gif'
                Image.new('RGB', (32,32), 'red').save(path, save_all=True,
                    append_images=[Image.new('RGB', (32,32), 'blue')], duration=100, loop=0)
                with patch('fntflshka_us6_clip.ui.gif_dialog.GifDialog.exec', return_value=1):
                    window.import_image(path)
                self.assertTrue(wait_for(lambda: window.prepared is not None))
                self.assertEqual(parse_asset(window.prepared.asset)['width'], 128)
                self.assertEqual(len(window.preview.animation), 2)
                with patch('fntflshka_us6_clip.ui.gif_dialog.GifDialog.exec', return_value=1), patch(
                        'fntflshka_us6_clip.ui.gif_dialog.GifDialog.options', return_value=('full',0,2,1)):
                    window.configure_gif()
                self.assertTrue(wait_for(lambda: window.prepared is not None))
                self.assertEqual(parse_asset(window.prepared.asset)['width'], 320)
                window.begin_upload()
                self.assertTrue(wait_for(lambda: not window.busy and len(window.snapshot.scenes)==1, timeout=20))
                with patch.object(window.device_worker.client,'read_scene', side_effect=AssertionError('No USB for preview')):
                    window.scenes.setCurrentRow(0)
                    self.assertTrue(wait_for(lambda: len(window.preview.animation)==2))
                    self.assertEqual(window.snapshot.scenes[0].type,2)
        finally:
            window.close()
            self.assertTrue(wait_for(lambda: not window.isVisible()))
