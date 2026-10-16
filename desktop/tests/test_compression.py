import struct
import unittest
import zlib
from dataclasses import replace
from PIL import Image, ImageDraw
from fntflshka_us6_clip.core.compression import encode_optimized, decoded_frames, rectangle_decode, ENTRY
from fntflshka_us6_clip.core.asset import parse_asset, rgb565_payload, first_frame
from fntflshka_us6_clip.media.animation import preview_frames, prepare_cached
from fntflshka_us6_clip.media.image import SceneSource, Placement
from fntflshka_us6_clip.device.client import DeviceClient
from fntflshka_us6_clip.device.mock import MockTransport


def moving(size=(128,128),count=70):
    result=[]
    for i in range(count):
        frame=Image.new('RGB',size,'navy')
        ImageDraw.Draw(frame).rectangle((i%(size[0]-12),20,i%(size[0]-12)+11,31),fill='pink')
        result.append(frame)
    return result


def repair(data):
    data=bytearray(data)
    struct.pack_into('<I',data,16,zlib.crc32(data[24:]))
    struct.pack_into('<I',data,20,zlib.crc32(data[:20]))
    return bytes(data)


class CompressionTests(unittest.TestCase):
    def test_oversize_measurement_and_prefix_sizes_match_actual_encoder(self):
        import random
        from fntflshka_us6_clip.core.animation import MAX_FILE
        rng=random.Random(7)
        frames=[Image.frombytes('RGB',(320,172),rng.randbytes(320*172*3)) for _ in range(8)]
        estimate=encode_optimized(frames,[350]*8,measure_only=True)
        self.assertEqual(estimate.file_size,40+8*(12+320*172*2))
        self.assertGreater(estimate.file_size,MAX_FILE)
        self.assertEqual(estimate.prefix_sizes[6],len(encode_optimized(frames[:7],[350]*7)))
        with self.assertRaises(ValueError):encode_optimized(frames,[350]*8)
        frames=moving(count=35)
        estimate=encode_optimized(frames,[250]*35,measure_only=True)
        for n in (2,7,31,32,33,35):
            self.assertEqual(estimate.prefix_sizes[n-1],len(encode_optimized(frames[:n],[250]*n)))

    def test_generated_crc_repeat_operators_match_zlib(self):
        import importlib.util
        from pathlib import Path
        import random
        root=Path(__file__).resolve().parents[2]
        spec=importlib.util.spec_from_file_location('crc_operators',root/'tools/generate_crc_operators.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual((root/'src/crc_pixel_shift.inc').read_text(encoding='utf-8'),module.source())
        operators=module.operators();rng=random.Random(42)
        for count in [1,2,3,31,32,33,255,256,257,32768,55040,65535]+[rng.randrange(1,65536) for _ in range(50)]:
            prefix=rng.randbytes(37);pixel=rng.randbytes(2)
            crc=zlib.crc32(prefix);part=zlib.crc32(pixel);left=count;power=0
            while left:
                if left&1:crc=module.apply(operators[power],crc)^part
                if left>1:part=module.apply(operators[power],part)^part
                power+=1;left>>=1
            self.assertEqual(crc,zlib.crc32(prefix+pixel*count))

    def test_delta_rle_keyframes_exact_pixels_and_loop(self):
        frames=moving()
        data=encode_optimized(frames,[250]*len(frames))
        info=parse_asset(data)
        self.assertEqual(info['encoding'],1)
        self.assertLess(len(data),40000)
        self.assertTrue(all(info['records'][i][-1] for i in (0,32,64)))
        self.assertTrue(any(not r[-1] for r in info['records']))
        self.assertEqual([p for p,_ in decoded_frames(data,info)],[rgb565_payload(f) for f in frames])
        self.assertEqual(first_frame(data),rgb565_payload(frames[0]))
        self.assertEqual(next(decoded_frames(data,info))[0],first_frame(data))
        self.assertEqual(len(preview_frames(data)),70)

    def test_noise_falls_back_to_legacy_without_expansion(self):
        import random
        randomizer=random.Random(123)
        frames=[Image.frombytes('RGB',(96,96),randomizer.randbytes(96*96*3)) for _ in range(3)]
        data=encode_optimized(frames,[170]*3)
        self.assertNotIn('encoding',parse_asset(data))
        self.assertEqual(len(data),40+3*(12+96*96*2))

    def test_malformed_records_and_rle_are_rejected(self):
        data=encode_optimized(moving(count=4),[250]*4)
        for offset,value,fmt in ((40,0,'I'),(44,0,'I'),(48,1,'I'),(56,320,'H'),
                                 (60,0,'H'),(64,2,'B'),(65,0,'B'),(66,1,'H')):
            damaged=bytearray(data)
            struct.pack_into('<'+fmt,damaged,offset,value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                parse_asset(repair(damaged))
        for encoded,pixels in ((b'\0\0\0\0',1),(b'\2\0\0\0',1),(b'\1\0\0\0',2),(b'\1',1)):
            with self.assertRaises(ValueError): rectangle_decode(encoded,1,pixels)
        damaged=bytearray(data)
        damaged[-1]^=1
        with self.assertRaises(ValueError): parse_asset(repair(damaged))

    def test_compressed_edit_archive_and_old_firmware_guard(self):
        from fntflshka_us6_clip.library.archive import Collection, CollectionScene, encode, decode
        frames=moving(count=4)
        data=encode_optimized(frames,[250]*4)
        source=SceneSource(data,(320,172))
        edited=prepare_cached(source,Placement(x=5))
        self.assertEqual(parse_asset(edited.asset)['x'],101)
        self.assertEqual(first_frame(edited.asset),first_frame(data))
        client=DeviceClient(MockTransport(budget=True))
        with self.assertRaisesRegex(ValueError,'1.8'): client.upload(data)
        client=DeviceClient(MockTransport(budget=True,compression=True))
        scene_id,_,after=client.upload(data)
        scene=next(s for s in after.scenes if s.id==scene_id)
        self.assertEqual(client.read_scene(scene,after.capabilities),data)
        from datetime import datetime,timezone
        collection=Collection('compressed',datetime.now(timezone.utc).isoformat(),(320,172),scene_id,
                              (CollectionScene(scene_id,'test',data),))
        self.assertEqual(decode(encode(collection)),collection)

    def test_preview_expansion_budget_is_bounded(self):
        frames=[Image.new('RGB',(320,172),'red')]*582
        with self.assertRaisesRegex(ValueError,'32 MP'):
            encode_optimized(frames,[350]*582)


class CompressionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_actual_optimized_size_enables_more_than_raw_limit_and_refreshes(self):
        import tempfile
        from pathlib import Path
        from test_gui_flow import wait_for
        from fntflshka_us6_clip.ui.gif_dialog import GifDialog
        from fntflshka_us6_clip.media.animation import GifSource,load_gif,prepare_gif
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'moving.gif'
            frames=moving((96,96),40)
            frames[0].save(path,save_all=True,append_images=frames[1:],duration=250,optimize=False)
            caps=DeviceClient(MockTransport(budget=True,compression=True)).snapshot().capabilities
            dialog=GifDialog(None,('full',0,0,1),path=path,caps=caps)
            dialog.show()
            try:
                self.assertTrue(wait_for(lambda: dialog.prepare_button.isEnabled(),timeout=30))
                self.assertIn('Экономия',dialog.memory.text())
                request=GifSource(path,'full',0,0,1,optimize=True)
                exact=len(prepare_gif(load_gif(path,request),request,Placement()).asset)
                self.assertEqual(dialog.analysis_result[1].file_size,exact)
                dialog.last.setValue(4)
                self.assertFalse(dialog.prepare_button.isEnabled())
                self.assertTrue(wait_for(lambda: dialog.prepare_button.isEnabled(),timeout=30))
                self.assertIn('4 из 40',dialog.memory.text())
            finally: dialog.close()

    def test_expanded_preview_cache_bounded_without_dropping_assets(self):
        from fntflshka_us6_clip.ui.main_window import MainWindow
        from fntflshka_us6_clip.media.animation import AnimationPreview
        window=MainWindow(mock=True,start_workers=False)
        try:
            window.cache_device='known'
            keys=[('demo',i,10+i,320,172) for i in range(2)]
            window.scene_previews={key:b'cached asset' for key in keys}
            frames=AnimationPreview([(Image.new('RGB',(320,172)),350)]*581,(320,172),(0,0))
            for key in keys:window.on_animation_preview(('known',key),frames)
            self.assertEqual(list(window.scene_animations),[keys[1]])
            self.assertEqual(len(window.scene_previews),2)
        finally:window.close()

    def test_oversize_gif_reports_exact_excess_and_fitting_last_frame(self):
        import tempfile,random
        from pathlib import Path
        from test_gui_flow import wait_for
        from fntflshka_us6_clip.ui.gif_dialog import GifDialog
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'noise.gif';rng=random.Random(8)
            frames=[Image.frombytes('RGB',(320,172),rng.randbytes(320*172*3)) for _ in range(8)]
            frames[0].save(path,save_all=True,append_images=frames[1:],duration=350,optimize=False)
            caps=DeviceClient(MockTransport(budget=True,compression=True)).snapshot().capabilities
            dialog=GifDialog(None,('full',0,0,1),path=path,caps=caps)
            dialog.show()
            try:
                self.assertTrue(wait_for(lambda: dialog.analysis_result is not None,timeout=30))
                self.assertIn('Превышен предел файла на 40.1 KiB',dialog.memory.text())
                self.assertIn('первые 7 из 8',dialog.memory.text())
                self.assertIn('«Последний кадр» = 7',dialog.memory.text())
                self.assertFalse(dialog.prepare_button.isEnabled())
                dialog.last.setValue(7)
                self.assertTrue(wait_for(lambda: dialog.prepare_button.isEnabled(),timeout=30))
                self.assertIn('Помещается',dialog.memory.text())
            finally:dialog.close()
