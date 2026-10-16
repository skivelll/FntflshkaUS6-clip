from dataclasses import replace,asdict
from pathlib import Path
import tempfile,json,zipfile
import unittest
from unittest.mock import patch
from PIL import Image
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase
from fntflshka_us6_clip.media.image import Placement,prepare_image
from fntflshka_us6_clip.media.text_model import TextLayer,validate_layers
from fntflshka_us6_clip.media.project import save_project,load_project
from fntflshka_us6_clip.core.asset import parse_asset
from fntflshka_us6_clip.media.rgb565 import decode_rgb565


class TextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.font=QFontDatabase.families()[0]

    def layer(self,**kwargs):return TextLayer(font=self.font,**kwargs)

    def test_worker_caches_background_and_draft_never_encodes_animation(self):
        from fntflshka_us6_clip.media.worker import ImageWorker
        from fntflshka_us6_clip.media.image import BlankSource,EditorRenderRequest
        from fntflshka_us6_clip.core.asset import encode_asset
        worker=ImageWorker();results=[];worker.ready.connect(lambda _,value:results.append(value))
        source=BlankSource(encode_asset(Image.new('RGB',(320,172))),(320,172))
        p=Placement(text_layers=(self.layer(),),text_motion='left')
        with patch('fntflshka_us6_clip.media.worker.prepare_image',wraps=prepare_image) as background:
            with patch('fntflshka_us6_clip.core.compression.encode_optimized',side_effect=AssertionError('Draft must not encode a movie')):
                worker.render(1,source,EditorRenderRequest(p,True))
                worker.render(2,source,EditorRenderRequest(replace(p,text_layers=(replace(p.text_layers[0],x=40),)),True))
            self.assertEqual(background.call_count,1)
            self.assertEqual(len(results),2);self.assertTrue(all(r.preview_only for r in results))
            worker.render(3,source,p)
            self.assertEqual(background.call_count,1)
            self.assertFalse(results[-1].preview_only);self.assertTrue(results[-1].frames)
        from fntflshka_us6_clip.media.image import scene_canvas
        self.assertEqual(results[-1].asset,prepare_image(scene_canvas(source),p).asset)

    def test_moving_draft_is_not_uploadable_until_idle_final_render(self):
        from fntflshka_us6_clip.ui.main_window import MainWindow
        from fntflshka_us6_clip.media.image import PreparedImage
        window=MainWindow(mock=True,start_workers=False)
        try:
            window.new_text_scene();window.text_controls.add();window.text_controls.motion.setCurrentIndex(1)
            self.assertTrue(window.text_animation_pending)
            window.on_rendered(window.generation,PreparedImage(b'',Image.new('RGB',(320,172)),preview_only=True))
            self.assertIsNone(window.prepared);self.assertFalse(window.upload_button.isEnabled())
            window.preview._drag_origin=object();window.finish_text_animation()
            self.assertTrue(window.text_animation_pending)
            window.preview._drag_origin=None;window.finish_text_animation()
            self.assertFalse(window.text_animation_pending)
        finally:window.close()

    def test_preview_clock_keeps_deadlines_and_skips_overdue_frames(self):
        from fntflshka_us6_clip.ui.preview import Preview
        preview=Preview();frames=tuple((Image.new('RGB',(2,2),color),350) for color in ('red','green','blue'))
        try:
            with patch('fntflshka_us6_clip.ui.preview.monotonic',return_value=0):preview.set_animation(frames)
            with patch('fntflshka_us6_clip.ui.preview.monotonic',return_value=.38):preview.advance_frame()
            self.assertEqual(preview.animation_index,1);self.assertAlmostEqual(preview.animation_deadline,.7)
            with patch('fntflshka_us6_clip.ui.preview.monotonic',return_value=1.5):preview.advance_frame()
            self.assertEqual(preview.animation_index,1);self.assertAlmostEqual(preview.animation_deadline,1.75)
            with patch('fntflshka_us6_clip.ui.preview.monotonic',return_value=100.1):preview.advance_frame()
            self.assertGreater(preview.animation_deadline,100.1)
            self.assertLessEqual(preview.animation_deadline,100.45)
        finally:preview.close()

    def test_static_cyrillic_exact_asset_and_source_unchanged(self):
        source=Image.new('RGBA',(640,344),'navy');previous=source.tobytes()
        base=prepare_image(source,Placement()).preview
        result=prepare_image(source,Placement(text_layers=(self.layer(text='Привет! Hello :3'),)))
        self.assertEqual(source.tobytes(),previous)
        self.assertNotEqual(base.tobytes(),result.preview.tobytes())
        self.assertEqual(decode_rgb565(result.asset[24:],(320,172)).tobytes(),result.preview.tobytes())
        self.assertNotEqual(parse_asset(result.asset).get('type'),2)

    def test_layer_order_background_outline_and_offscreen_clip(self):
        a=self.layer(text='AAAA',color='#ff0000',background=True,background_color='#ff0000')
        b=replace(a,color='#0000ff',background_color='#0000ff')
        source=Image.new('RGB',(320,172))
        first=prepare_image(source,Placement(text_layers=(a,b)))
        second=prepare_image(source,Placement(text_layers=(b,a)))
        self.assertNotEqual(first.asset,second.asset)
        off=prepare_image(source,Placement(text_layers=(replace(a,x=-640,y=-640),)))
        self.assertIsNone(off.preview.getbbox())

    def test_scroll_both_directions_blank_seam_and_exact_frames(self):
        source=Image.new('RGB',(320,172),'navy')
        for direction in ('left','right'):
            p=Placement(text_layers=(self.layer(text='Я :3',width=100),),text_motion=direction,text_speed=120,text_pause=500)
            result=prepare_image(source,p);info=parse_asset(result.asset)
            self.assertEqual(info['type'],2)
            self.assertGreater(len(result.frames),2)
            first=result.frames[0][0];last=result.frames[-1][0]
            self.assertEqual(first.tobytes(),last.tobytes())
            self.assertTrue(any(frame.tobytes()!=first.tobytes() for frame,_ in result.frames))
            self.assertGreaterEqual(result.frames[0][1],850)
            self.assertIn('Бегущий текст',result.summary)

    def test_validation_and_missing_font_are_explicit(self):
        for layer in (self.layer(text='x'*513),self.layer(size=7),self.layer(color='red')):
            with self.assertRaises(ValueError):validate_layers((layer,))
        with self.assertRaises(ValueError):validate_layers((self.layer(),)*9)
        with self.assertRaisesRegex(ValueError,'не установлен'):
            prepare_image(Image.new('RGB',(320,172)),Placement(text_layers=(replace(self.layer(),font='missing-test-font-92837'),)))

    def test_project_retains_layers_and_legacy_v1_loads(self):
        p=Placement(text_layers=(self.layer(text='Два слоя'),self.layer(text='Second',y=80)),text_motion='right')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'text.fclipimage'
            save_project(path,Image.new('RGBA',(320,172)),p)
            source,loaded=load_project(path);self.assertEqual(loaded,p)
            with zipfile.ZipFile(path) as archive:pixels=archive.read('source.png')
            values=asdict(Placement())
            for key in ('text_layers','text_motion','text_speed','text_pause'):values.pop(key)
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('source.png',pixels);archive.writestr('settings.json',json.dumps({'version':1,'placement':values}))
            self.assertEqual(load_project(path)[1].text_layers,())

    def test_gui_layers_undo_redo_drag_and_blank_without_usb(self):
        from fntflshka_us6_clip.ui.main_window import MainWindow
        window=MainWindow(mock=True,start_workers=False)
        try:
            controls=window.text_controls;controls.add()
            self.assertIsNotNone(window.image_source)
            controls.text.setPlainText('Привет')
            controls.add();controls.text.setPlainText('Second')
            controls.move(-1);self.assertEqual(controls.layers[0].text,'Second')
            controls.undo();self.assertEqual(controls.layers[1].text,'Second')
            controls.redo();self.assertEqual(controls.layers[0].text,'Second')
            window.preview.set_interactive(True);window.settings_tabs.setCurrentIndex(1)
            window.begin_drag();window.drag_image(10,15)
            self.assertEqual((controls.selected().x,controls.selected().y),(22,27))
            self.assertEqual(window.controls.x.value(),0)
            controls.remove();self.assertEqual(len(controls.layers),1)
            window.cancel_edit();self.assertEqual(controls.layers,())
        finally:window.close()
