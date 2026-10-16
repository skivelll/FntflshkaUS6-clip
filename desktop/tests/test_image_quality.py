from dataclasses import replace,asdict
from pathlib import Path
import tempfile
import unittest
import zipfile
import json
from unittest.mock import patch
from PIL import Image
from PySide6.QtWidgets import QApplication
from fntflshka_us6_clip.media.image import Placement,prepare_image
from fntflshka_us6_clip.media.rgb565 import decode_rgb565
from fntflshka_us6_clip.media.project import save_project,load_project
from fntflshka_us6_clip.core.asset import parse_asset


class ImageQualityTests(unittest.TestCase):
    def test_workspace_columns_and_settings_fit_full_hd(self):
        from fntflshka_us6_clip.ui.main_window import MainWindow
        app=QApplication.instance() or QApplication([])
        window=MainWindow(mock=True,start_workers=False)
        try:
            window.resize(1920,1000);window.show();app.processEvents()
            self.assertEqual(window.columns.count(),3)
            self.assertTrue(window.scene_panel.isAncestorOf(window.scenes))
            for widget in (window.delete_button,window.storage_bar,window.upload_button):
                self.assertTrue(window.scene_actions_panel.isAncestorOf(widget))
            self.assertTrue(window.settings_panel.isAncestorOf(window.controls))
            scroll=window.controls.parentWidget().parentWidget()
            self.assertEqual(scroll.verticalScrollBar().maximum(),0)
            window.resize(1280,800);app.processEvents()
            self.assertLessEqual(window.width(),1280)
            self.assertGreater(window.preview.width(),320)
        finally:window.close()
    def test_dithering_reduces_flat_gradient_average_bias(self):
        source=Image.new('RGB',(320,172),(128,128,128))
        plain=prepare_image(source,Placement(sharpness=0),comparison=False).preview
        dithered=prepare_image(source,Placement(sharpness=0,dithering=True),comparison=False).preview
        for channel in (0,1,2):
            mean=lambda image:sum(image.tobytes()[channel::3])/(320*172)
            self.assertLess(abs(mean(dithered)-128),abs(mean(plain)-128))

    def test_failed_project_commit_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'saved.fclipimage'
            save_project(path,Image.new('RGB',(2,2),'red'),Placement())
            previous=path.read_bytes()
            with patch('fntflshka_us6_clip.media.project.os.replace',side_effect=OSError('disk unavailable')):
                with self.assertRaises(OSError):save_project(path,Image.new('RGB',(2,2),'blue'),Placement())
            self.assertEqual(path.read_bytes(),previous)
            self.assertEqual(list(Path(folder).iterdir()),[path])

    def test_exact_output_and_source_immutable(self):
        source=Image.linear_gradient('L').resize((640,344)).convert('RGBA')
        original=source.tobytes()
        result=prepare_image(source,Placement(dithering=True,sharpness=40))
        self.assertEqual(result.preview.tobytes(),decode_rgb565(result.asset[24:],(320,172)).tobytes())
        self.assertEqual(source.tobytes(),original)
        self.assertNotEqual(result.before.tobytes(),result.preview.tobytes())
        self.assertEqual(parse_asset(result.asset)['width'],320)

    def test_pixel_art_ignores_filters_and_gif_stays_legacy(self):
        from fntflshka_us6_clip.media.animation import prepare_image as gif_prepare
        source=Image.new('RGB',(7,3),'red');source.putpixel((3,1),(0,255,0))
        p=Placement(pixel_art=True,dithering=True,sharpness=100)
        self.assertEqual(prepare_image(source,p).asset,prepare_image(source,replace(p,dithering=False,sharpness=0,resampling='bicubic')).asset)
        self.assertEqual(gif_prepare(source,Placement(dithering=True,sharpness=100)).asset,
                         prepare_image(source,Placement(resampling='bicubic',sharpness=0),comparison=False).asset)

    def test_transparency_crop_and_offscreen_panorama(self):
        source=Image.new('RGBA',(10000,10),(255,0,0,0))
        for mode in ('fit','fill'):
            result=prepare_image(source,Placement(mode=mode,scale=400,background='#00ff00'))
            self.assertEqual(set(result.preview.getdata()),{(0,255,0)})
        result=prepare_image(Image.new('RGB',(2,2),'red'),Placement(x=640,y=640))
        self.assertEqual(set(result.preview.getdata()),{(0,0,0)})

    def test_project_roundtrip_and_reject_untrusted_entries(self):
        source=Image.new('RGBA',(15,9),(10,25,40,100))
        placement=Placement(scale=150,x=4,dithering=True,sharpness=53)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'example.fclipimage'
            save_project(path,source,placement)
            loaded,p=load_project(path)
            self.assertEqual(p,placement);self.assertEqual(loaded.tobytes(),source.tobytes())
            self.assertEqual(prepare_image(loaded,p).asset,prepare_image(source,placement).asset)
            with zipfile.ZipFile(path,'a') as archive:archive.writestr('../outside.png',b'bad')
            with self.assertRaises(ValueError):load_project(path)
            self.assertFalse((Path(folder).parent/'outside.png').exists())

    def test_project_rejects_invalid_settings_without_clamping(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bad.fclipimage';save_project(path,Image.new('RGB',(1,1)),Placement())
            with zipfile.ZipFile(path) as archive:pixels=archive.read('source.png')
            data=asdict(Placement());data['sharpness']=500
            data.pop('paint_pixels',None)
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('source.png',pixels);archive.writestr('settings.json',json.dumps({'version':1,'placement':data}))
            with self.assertRaises(ValueError):load_project(path)

    def test_controls_roundtrip_and_compare_dialog(self):
        from fntflshka_us6_clip.ui.widgets import PlacementControls
        from fntflshka_us6_clip.ui.comparison import ComparisonDialog
        app=QApplication.instance() or QApplication([])
        controls=PlacementControls();controls.restore_preferences({'resampling':'bicubic','sharpness':65,'dithering':True})
        self.assertEqual(controls.placement().sharpness,65);self.assertTrue(controls.placement().dithering)
        controls.sharpness_up.click();self.assertEqual(controls.sharpness.value(),66)
        controls.sharpness_down.click();self.assertEqual(controls.sharpness.value(),65)
        controls.sharpness.setValue(100);controls.sharpness_up.click();self.assertEqual(controls.sharpness.value(),100)
        controls.sharpness.setValue(0);controls.sharpness_down.click();self.assertEqual(controls.sharpness.value(),0)
        controls.pixel_art.setChecked(True);self.assertFalse(controls.quality.isEnabled())
        result=prepare_image(Image.new('RGB',(10,10),'blue'),Placement())
        dialog=ComparisonDialog(result.before,result.preview);dialog.show();app.processEvents();dialog.close();controls.close()
