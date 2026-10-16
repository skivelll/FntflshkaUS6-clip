import errno
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PIL import Image
from fntflshka_us6_clip.problems import problem
from fntflshka_us6_clip.core.protocol import DeviceNack
from fntflshka_us6_clip.core.asset import encode_asset
from fntflshka_us6_clip.library.restore import RestoreFailure
from fntflshka_us6_clip.ui.notifications import Notifications,ProblemDialog
from fntflshka_us6_clip.ui.main_window import MainWindow


class ProblemTests(unittest.TestCase):
    def test_friendly_nack_and_exact_technical_report(self):
        error=DeviceNack(4);error.command=7;error.sequence=123;error.port='COM-test'
        value=problem(error,'upload')
        self.assertEqual(value.title,'Не хватает места')
        self.assertNotIn('NACK',value.message)
        report=value.report()
        for expected in ('DeviceNack','NO_SPACE','"command": 7','"sequence": 123','COM-test','Automatic write retry: disabled'):
            self.assertIn(expected,report)
        self.assertIn('не подтверждено',value.state)
        self.assertIn('не записывала',problem(ValueError('bad CRC'),'preview').state)
        self.assertEqual(problem(ValueError('unexpected firmware identity'),'connection').title,'Устройство не распознано')

    def test_partial_restore_keeps_acknowledged_and_uncertain_ids(self):
        failure=RestoreFailure(TimeoutError('END reply missing'),[1,2],3)
        value=problem(failure,'restore')
        self.assertIn('[1, 2]',value.state);self.assertIn('3',value.state)
        self.assertIn('"attempted_id": 3',value.report())
        self.assertIn('END reply missing',value.report())

    def test_disk_port_and_bounded_exception_chain(self):
        self.assertIn('на компьютере',problem(OSError(errno.ENOSPC,'disk full'),'library').action)
        self.assertEqual(problem(PermissionError('Access denied'),'connection').title,'Подключение занято')
        try:
            try:raise PermissionError('original backup permission error')
            except OSError as error:raise ValueError('backup not written') from error
        except ValueError as error:value=problem(error,'restore')
        self.assertIn('original backup permission error',value.report())
        self.assertLessEqual(len(value.report({'long':'x'*100000})),65536)
        self.assertNotIn('secret pixels',value.report({'binary':b'secret pixels'}))


class NotificationGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_details_copy_and_event_loop_keep_running(self):
        dialog=ProblemDialog();value=problem(TimeoutError('USB timeout'),'upload')
        dialog.display(value,value.report({'port':'COM-test'}));dialog.show()
        timer=QTimer();ticks=[];timer.timeout.connect(lambda:ticks.append(1));timer.start(10)
        try:
            dialog.toggle.setChecked(True)
            self.assertTrue(dialog.details.isVisible())
            self.assertTrue(dialog.details.isReadOnly())
            clipboard=Mock()
            with patch.object(QApplication,'clipboard',return_value=clipboard):dialog.copy_report()
            clipboard.setText.assert_called_once_with(dialog.details.toPlainText())
            QTest.qWait(80);self.assertGreater(len(ticks),2)
        finally:timer.stop();dialog.close()

    def test_connection_dedup_recovery_and_important_failure_priority(self):
        window=MainWindow(mock=True,start_workers=False);notices=[]
        window.notifications.changed.connect(notices.append)
        try:
            missing=problem(FileNotFoundError('none'),'connection')
            for _ in range(3):window.notifications.present(missing)
            self.assertEqual(len(notices),1)
            window.notifications.recovered('connection');window.notifications.present(missing)
            self.assertEqual(len(notices),2)
            upload=problem(TimeoutError('upload result unknown'),'upload')
            window.notifications.present(upload)
            window.notifications.recovered('connection');window.notifications.present(missing)
            self.assertIs(window.notifications.last,upload)
            window.notifications.shutdown();window.notifications.present(problem(ValueError('x'),'image'))
            self.assertIsNone(window.notifications.dialog)
        finally:window.close()

    def test_reconnected_board_clears_only_connection_banner(self):
        window=MainWindow(mock=True,start_workers=False)
        try:
            value=problem(TimeoutError('USB disconnected'),'connection')
            window.notifications.present(value)
            self.assertFalse(window.problem_banner.isHidden())
            window.notifications.recovered('connection') # Rearming is not success.
            self.assertFalse(window.problem_banner.isHidden())
            window.notifications.resolve('connection')
            self.assertTrue(window.problem_banner.isHidden())
            self.assertIs(window.notifications.last,value) # Report remains available.
            window.notifications.dialog.accept()
            window.notifications.present(problem(TimeoutError('END missing'),'upload'))
            window.notifications.resolve('connection')
            self.assertFalse(window.problem_banner.isHidden())
        finally:window.close()

    def test_old_render_failure_is_ignored_and_sound_is_opt_in(self):
        window=MainWindow(mock=True,start_workers=False)
        try:
            self.assertFalse(window.notifications.sound)
            window.generation=10
            window.on_media_problem(9,problem(ValueError('old source'),'image'))
            self.assertIsNone(window.notifications.last)
            with patch.object(QApplication,'beep') as beep:
                window.on_media_problem(10,problem(ValueError('current'),'image'));beep.assert_not_called()
                window.error_sound.setChecked(True)
                window.on_media_problem(10,problem(ValueError('different'),'image'));beep.assert_called_once()
        finally:window.close()

    def test_explicit_new_import_rearms_same_error(self):
        window=MainWindow(mock=True,start_workers=False)
        try:
            value=problem(ValueError('bad file'),'image',context={'source_path':'same.png'})
            window.notifications.present(value)
            window.notifications.dialog.accept()
            window.import_image(Path('same.png'))
            window.notifications.present(value)
            self.assertTrue(window.notifications.dialog.isVisible())
        finally:window.close()

    def test_distinct_errors_wait_for_acknowledgement(self):
        window=MainWindow(mock=True,start_workers=False)
        try:
            first=problem(ValueError('first'),'image');second=problem(ValueError('second'),'library')
            window.notifications.present(first);window.notifications.present(second)
            self.assertIs(window.notifications.last,first)
            self.assertEqual(len(window.notifications.pending),1)
            self.assertIn('1',window.notifications.dialog.queued.text())
            window.notifications.dialog.accept();QApplication.processEvents()
            self.assertIs(window.notifications.last,second)
            self.assertTrue(window.notifications.dialog.isVisible())
        finally:window.close()

    def test_error_survives_transient_modal_parent_destruction(self):
        from PySide6.QtWidgets import QDialog
        from PySide6.QtCore import Qt,QEvent,QCoreApplication
        window=MainWindow(mock=True,start_workers=False)
        modal=QDialog(window);modal.setWindowModality(Qt.ApplicationModal);modal.show()
        QApplication.processEvents()
        try:
            window.notifications.present(problem(ValueError('modal failure'),'validation'))
            self.assertIs(window.notifications.dialog.parentWidget(),modal)
            modal.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
            QApplication.processEvents()
            self.assertIsNotNone(window.notifications.dialog)
            self.assertIs(window.notifications.dialog.parentWidget(),window)
            self.assertTrue(window.notifications.dialog.isVisible())
        finally:window.close()

    def test_acknowledged_error_does_not_reappear_when_parent_is_deleted(self):
        from PySide6.QtWidgets import QDialog
        from PySide6.QtCore import Qt,QEvent,QCoreApplication
        window=MainWindow(mock=True,start_workers=False)
        modal=QDialog(window);modal.setWindowModality(Qt.ApplicationModal);modal.show()
        QApplication.processEvents()
        try:
            window.notifications.present(problem(ValueError('already read'),'validation'))
            window.notifications.dialog.accept()
            modal.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
            QApplication.processEvents()
            self.assertIsNone(window.notifications.dialog)
            self.assertFalse(window.notifications.unread)
        finally:window.close()

    def test_sound_preference_roundtrip(self):
        from fntflshka_us6_clip.settings import Preferences
        with tempfile.TemporaryDirectory() as directory:
            prefs=Preferences(Path(directory)/'settings.ini')
            window=MainWindow(mock=True,start_workers=False,preferences=prefs)
            window.error_sound.setChecked(True);window.close()
            other=MainWindow(mock=True,start_workers=False,preferences=prefs)
            try:self.assertTrue(other.notifications.sound)
            finally:other.close()

    def test_preferences_save_failure_is_visible_once_before_close(self):
        window=MainWindow(mock=True,start_workers=False)
        prefs=Mock();prefs.save.return_value=False;window.preferences=prefs
        window.show();window.close()
        self.assertEqual(window.notifications.last.operation,'settings')
        self.assertTrue(window.notifications.dialog.isVisible())
        self.assertTrue(window.isVisible())
        window.notifications.dialog.accept()
        QApplication.processEvents()
        self.assertFalse(window.isVisible())
        prefs.save.assert_called_once()

    def test_upload_failure_has_context_without_asset_bytes_and_no_retry(self):
        from fntflshka_us6_clip.device.worker import DeviceWorker
        worker=DeviceWorker(mock=True);failures=[];worker.failure.connect(failures.append)
        with patch('serial.Serial',side_effect=AssertionError('mock touched USB')):
            worker.start()
            try:
                with patch.object(worker.client,'upload',side_effect=TimeoutError('END ack timeout')) as upload:
                    worker.perform_upload(encode_asset(Image.new('RGB',(8,8),'red')))
                    upload.assert_called_once()
                self.assertEqual(len(failures),1)
                self.assertEqual(failures[0].operation,'upload')
                self.assertIn('asset_bytes',failures[0].report())
                self.assertNotIn("b'HCAS",failures[0].report())
                self.assertIn('не подтверждено',failures[0].state)
            finally:worker.stop()

    def test_post_commit_preview_error_does_not_claim_write_failed(self):
        from fntflshka_us6_clip.device.worker import DeviceWorker
        worker=DeviceWorker(mock=True);failures=[];worker.failure.connect(failures.append);worker.start()
        try:
            with patch.object(worker,'preload',side_effect=OSError('cache refresh failed')):
                worker.perform_upload(encode_asset(Image.new('RGB',(8,8),'blue')))
            self.assertIn('записана и подтверждена',failures[0].state)
            self.assertEqual(failures[0].context['committed_id'],1)
            self.assertEqual(len(worker.mock_transport.scenes),1)
        finally:worker.stop()

    def test_image_and_archive_workers_keep_original_failure(self):
        from fntflshka_us6_clip.media.worker import ImageWorker
        from fntflshka_us6_clip.media.image import Placement
        from fntflshka_us6_clip.library.worker import LibraryWorker
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'missing.png'
            image=ImageWorker();images=[];image.failure.connect(lambda generation,value:images.append((generation,value)))
            image.render(7,source,Placement())
            self.assertEqual(images[0][0],7);self.assertIn('FileNotFoundError',images[0][1].report())
            broken=Path(directory)/'broken.fclip';broken.write_bytes(b'not a zip')
            library=LibraryWorker(Path(directory)/'library');errors=[];library.failure.connect(errors.append)
            library.run(('import',broken))
            self.assertEqual(errors[0].operation,'library');self.assertIn('Traceback',errors[0].report())
