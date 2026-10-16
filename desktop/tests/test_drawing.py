from dataclasses import replace
from pathlib import Path
import io,tempfile,zipfile
import unittest
from unittest.mock import patch
from PIL import Image
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtCore import Qt,QPointF
from PySide6.QtTest import QTest
from fntflshka_us6_clip.media.drawing import DrawingDocument,flood
from fntflshka_us6_clip.media.image import Placement,prepare_image
from fntflshka_us6_clip.media.project import save_project,load_project


class DrawingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_strokes_erase_undo_and_bounded_history(self):
        doc=DrawingDocument();doc.begin((2,2),'#ff0000',1);doc.line((20,20));doc.end()
        self.assertEqual(doc.image.getpixel((10,10)),(255,0,0,255))
        previous=doc.pixels();doc.begin((10,10),'#ffffff',3,erase=True);doc.end()
        self.assertEqual(doc.image.getpixel((10,10))[3],0)
        doc.undo();self.assertEqual(doc.pixels(),previous);doc.redo();self.assertEqual(doc.image.getpixel((10,10))[3],0)
        for i in range(60):doc.begin((i,0),'#ffffff',1);doc.end()
        self.assertEqual(len(doc.history),51)
        doc.clear();self.assertEqual(doc.pixels(),b'');doc.undo();self.assertTrue(doc.pixels())

    def test_fill_bounds_and_separate_layer(self):
        doc=DrawingDocument((10,10));doc.begin((0,5),'#ffffff',1);doc.line((9,5));doc.end()
        filled=flood(doc.image.tobytes(),doc.target,(0,0),'#ff0000');doc.replace(filled)
        self.assertEqual(doc.image.getpixel((2,2)),(255,0,0,255));self.assertEqual(doc.image.getpixel((2,8))[3],0)
        with self.assertRaises(ValueError):flood(filled,doc.target,(11,0),'#ffffff')

    def test_exact_preview_and_layer_above_text_preserves_source(self):
        from fntflshka_us6_clip.media.text_model import TextLayer
        from PySide6.QtGui import QFontDatabase
        source=Image.new('RGBA',(640,344),'blue');previous=source.tobytes()
        doc=DrawingDocument();doc.begin((15,20),'#ff0000',16);doc.end()
        p=Placement(paint_pixels=doc.pixels(),text_layers=(TextLayer(font=QFontDatabase.families()[0],text='Hello'),))
        result=prepare_image(source,p)
        self.assertEqual(result.preview.getpixel((15,20)),(255,0,0));self.assertEqual(source.tobytes(),previous)
        from fntflshka_us6_clip.media.rgb565 import decode_rgb565
        self.assertEqual(result.preview.tobytes(),decode_rgb565(result.asset[24:],(320,172)).tobytes())

    def test_project_roundtrip_and_invalid_drawing_dimensions(self):
        doc=DrawingDocument();doc.begin((10,10),'#abcdef',3);doc.end()
        p=Placement(paint_pixels=doc.pixels())
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'drawing.fclipimage';save_project(path,Image.new('RGBA',(10,10)),p)
            _,loaded=load_project(path);self.assertEqual(loaded,p)
            with zipfile.ZipFile(path) as archive:entries={n:archive.read(n) for n in archive.namelist()}
            wrong=io.BytesIO();Image.new('RGBA',(1,1)).save(wrong,format='PNG');entries['drawing.png']=wrong.getvalue()
            with zipfile.ZipFile(path,'w') as archive:
                for name,data in entries.items():archive.writestr(name,data)
            with self.assertRaisesRegex(ValueError,'размер'):load_project(path)

    def test_preview_pixel_coordinates_zoom_and_stroke(self):
        from fntflshka_us6_clip.ui.main_window import MainWindow
        window=MainWindow(mock=True,start_workers=False)
        try:
            window.new_drawing_scene();window.preview.set_image(Image.new('RGB',(320,172)));window.update_memory()
            window.show();self.app.processEvents()
            for zoom in (1,4,8):
                window.draw_controls.zoom.setCurrentIndex(window.draw_controls.zoom.findData(zoom))
                rect=window.preview.display_rect();position=QPointF(rect.x()+10.5*rect.width()/320,rect.y()+20.5*rect.height()/172)
                self.assertEqual(window.preview.pixel_position(position),(10,20))
            window.draw_controls.zoom.setCurrentIndex(0);rect=window.preview.display_rect()
            point=QPointF(rect.x()+10.5*rect.width()/320,rect.y()+20.5*rect.height()/172).toPoint()
            QTest.mouseClick(window.preview,Qt.LeftButton,pos=point)
            self.assertEqual(window.draw_controls.document.image.getpixel((10,20))[3],255)
            old=window.draw_controls.document.pixels()
            with patch('fntflshka_us6_clip.ui.main_window.QMessageBox.question',return_value=QMessageBox.No):window.clear_drawing()
            self.assertEqual(window.draw_controls.document.pixels(),old)
            window.draw_controls.undo();self.assertEqual(window.draw_controls.document.pixels(),b'')
        finally:window.close()

    def test_stale_fill_and_cached_background(self):
        from fntflshka_us6_clip.ui.main_window import MainWindow
        from fntflshka_us6_clip.media.worker import ImageWorker
        from fntflshka_us6_clip.media.image import BlankSource
        from fntflshka_us6_clip.core.asset import encode_asset
        window=MainWindow(mock=True,start_workers=False)
        try:
            window.new_drawing_scene();before=window.draw_controls.document.pixels()
            window.on_fill_ready((window.source_revision-1,0,bytes(320*172*4)))
            self.assertEqual(window.draw_controls.document.pixels(),before)
        finally:window.close()
        worker=ImageWorker();results=[];worker.ready.connect(lambda _,r:results.append(r))
        source=BlankSource(encode_asset(Image.new('RGB',(320,172))),(320,172));doc=DrawingDocument()
        with patch('fntflshka_us6_clip.media.worker.prepare_image',wraps=prepare_image) as prepare:
            for i in range(2):
                doc.begin((i,0),'#ffffff',1);doc.end();worker.render(i,source,Placement(paint_pixels=doc.pixels()))
            self.assertEqual(prepare.call_count,1)
        self.assertEqual(len(results),2)
