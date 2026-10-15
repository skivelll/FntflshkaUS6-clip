from dataclasses import replace
import io
import json
from pathlib import Path
import stat
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import warnings
import zipfile
from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from fntflshka_us6_clip.core.asset import encode_asset
from fntflshka_us6_clip.device.client import DeviceClient
from fntflshka_us6_clip.device.mock import MockTransport
from fntflshka_us6_clip.library.archive import (Collection, CollectionScene, encode, decode, save, load, from_snapshot)
from fntflshka_us6_clip.library.restore import make_plan, execute, RestoreFailure
from fntflshka_us6_clip.ui.main_window import MainWindow
from fntflshka_us6_clip.ui.restore_dialog import RestoreDialog
from test_gui_flow import wait_for


def collection():
    return Collection('Коллекция "♥"', "2026-10-15T10:00:00+00:00", (320, 172), 2,
        (CollectionScene(1, "Красный", encode_asset(Image.new("RGB", (2, 2), "red"))),
         CollectionScene(2, "Синий", encode_asset(Image.new("RGB", (3, 2), "blue")))))


def rewrite(data, change):
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    change(files)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, data in files.items():
            target.writestr(name, data)
    return out.getvalue()


class ArchiveTests(unittest.TestCase):
    def test_roundtrip_preserves_pixels_names_order_and_active(self):
        original = collection()
        self.assertEqual(decode(encode(original)), original)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "заколка.fclip"
            save(path, original)
            self.assertEqual(load(path), original)

    def test_export_cache_only_and_selected_subset(self):
        transport = MockTransport()
        client = DeviceClient(transport)
        assets = {}
        for source in collection().scenes:
            result = client.upload(source.asset)
            s = result[2].scenes[-1]
            assets[(transport.port, s.id, s.checksum, s.width, s.height)] = source.asset
        snapshot = client.snapshot()
        selected = from_snapshot(snapshot, assets, "Одна", 2)
        self.assertEqual([s.id for s in selected.scenes], [2])
        self.assertEqual(selected.active_id, 2)
        with self.assertRaises(ValueError):
            from_snapshot(snapshot, {}, "Нет кэша")

    def test_rejects_corruption_paths_extra_files_and_unknown_version(self):
        data = encode(collection())
        def version(files):
            manifest = json.loads(files["collection.json"])
            manifest["version"] = 2
            files["collection.json"] = json.dumps(manifest).encode()
        def path(files):
            manifest = json.loads(files["collection.json"])
            manifest["scenes"][0]["asset"] = "../../outside.bin"
            files["collection.json"] = json.dumps(manifest).encode()
        for change in (version, path, lambda f: f.update({"extra.bin": b"x"}),
                       lambda f: f.update({"assets/0001.bin": b"bad"}),
                       lambda f: f.update({"collection.json": b'{"format":1,"format":2}'}),
                       lambda f: f.update({"assets/0001.bin": b"x" * 110105})):
            with self.assertRaises(ValueError):
                decode(rewrite(data, change))
        with self.assertRaises(ValueError):
            decode(b"x" * 2_000_001)

    def test_rejects_duplicate_entries_and_symlinks(self):
        out = io.BytesIO(encode(collection()))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(out, "a") as archive:
                archive.writestr("collection.json", "{}")
        with self.assertRaises(ValueError):
            decode(out.getvalue())
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("collection.json", "{}")
            link = zipfile.ZipInfo("assets/0001.bin")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, b"../../secret")
        with self.assertRaises(ValueError):
            decode(out.getvalue())

    def test_failed_atomic_save_keeps_previous_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "backup.fclip"
            old = collection()
            save(path, old)
            with patch("fntflshka_us6_clip.library.archive.os.replace", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    save(path, replace(old, title="Новое"))
            self.assertEqual(load(path), old)
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["backup.fclip"])


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.transport = MockTransport()
        self.client = DeviceClient(self.transport)

    def test_empty_device_restores_original_ids_titles_order_and_active(self):
        plan = make_plan(collection(), self.client.snapshot(), ["add", "add"])
        after = execute(self.client, plan)
        self.assertEqual([s.id for s in after.scenes], [1, 2])
        self.assertEqual([s.title for s in after.scenes], ["Красный", "Синий"])
        self.assertTrue(after.scenes[1].active)
        self.assertEqual(self.transport.assets[1], collection().scenes[0].asset)

    def test_empty_device_keeps_non_numeric_archive_order_and_original_ids(self):
        c = replace(collection(), scenes=tuple(reversed(collection().scenes)), active_id=1)
        after = execute(self.client, make_plan(c, self.client.snapshot(), ["add", "add"]))
        self.assertEqual([s.id for s in after.scenes], [2, 1])
        self.assertTrue(after.scenes[1].active)

    def test_explicit_replacement_keeps_unrelated_scene_and_never_deletes(self):
        old = encode_asset(Image.new("RGB", (2, 2), "green"))
        self.client.upload(old)
        self.client.upload(old)
        baseline = self.client.snapshot()
        calls = []
        original = self.transport.request
        def request(command, *args, **kwargs):
            calls.append(command)
            return original(command, *args, **kwargs)
        with patch.object(self.transport, "request", side_effect=request):
            plan = make_plan(collection(), baseline, [1, "add"])
            after = execute(self.client, plan)
        self.assertEqual([s.id for s in after.scenes], [2, 1, 3])
        self.assertEqual(self.transport.assets[2], old)
        self.assertEqual(self.transport.assets[1], collection().scenes[0].asset)
        self.assertNotIn(8, calls)
        self.assertTrue(next(s for s in after.scenes if s.id == 3).active)

    def test_preflight_rejects_full_ids_duplicates_space_and_stale_baseline(self):
        old = encode_asset(Image.new("RGB", (1, 1)))
        for _ in range(8):
            self.client.upload(old)
        snapshot = self.client.snapshot()
        for targets in (["add", "add"], [1, 1]):
            with self.assertRaises(ValueError):
                make_plan(collection(), snapshot, targets)
        with self.assertRaises(ValueError):
            make_plan(collection(), replace(snapshot, capabilities=replace(snapshot.capabilities, storage_free=8192)), [1, 2])
        plan = make_plan(collection(), snapshot, [1, 2])
        self.client.rename_scene(1, snapshot.scenes[0].checksum, "Изменено")
        before = dict(self.transport.assets)
        with self.assertRaises(RestoreFailure) as error:
            execute(self.client, plan)
        self.assertEqual(error.exception.committed, ())
        self.assertEqual(self.transport.assets, before)

    def test_partial_disconnect_stops_without_retry_and_preserves_committed_scene(self):
        c = replace(collection(), scenes=tuple(replace(s, asset=encode_asset(Image.new("RGB", (40, 40), "red"))) for s in collection().scenes))
        plan = make_plan(c, self.client.snapshot(), ["add", "add"])
        begins = []
        original = self.transport.request
        def request(command, *args, **kwargs):
            if command == 5:
                begins.append(int.from_bytes(args[0][:4], "little"))
            if command == 6 and self.transport.upload[0] == 2:
                raise OSError("Cable disconnected")
            return original(command, *args, **kwargs)
        with patch.object(self.transport, "request", side_effect=request):
            with self.assertRaises(RestoreFailure) as error:
                execute(self.client, plan)
        self.assertEqual(error.exception.committed, (1,))
        self.assertEqual(begins, [1, 2])
        self.assertEqual([s.id for s in self.transport.scenes], [1])

    def test_cancel_and_old_metadata_capabilities(self):
        plan = make_plan(collection(), self.client.snapshot(), ["add", "add"])
        cancel = Event()
        with self.assertRaises(RestoreFailure) as error:
            execute(self.client, plan, ready=lambda *args: cancel.set(), cancelled=cancel.is_set)
        self.assertEqual(error.exception.committed, (1,))
        self.transport = MockTransport()
        self.transport.caps = replace(self.transport.caps, reserved=1)
        self.client = DeviceClient(self.transport)
        with self.assertRaises(ValueError):
            make_plan(collection(), self.client.snapshot(), ["add", "add"])
        after = execute(self.client, make_plan(collection(), self.client.snapshot(), ["add", "add"], metadata=False))
        self.assertEqual(len(after.scenes), 2)

    def test_changed_identity_and_dimensions_are_rejected_before_upload(self):
        snapshot = self.client.snapshot()
        plan = make_plan(collection(), snapshot, ["add", "add"])
        self.transport.identity_key = "different-board"
        with self.assertRaises(RestoreFailure):
            execute(self.client, plan)
        self.assertIsNone(self.transport.upload)
        smaller = replace(snapshot, capabilities=replace(snapshot.capabilities, display_width=1, display_height=1))
        with self.assertRaises(ValueError):
            make_plan(collection(), smaller, ["add", "add"])

    def test_backup_write_failure_prevents_any_device_upload(self):
        from fntflshka_us6_clip.device.worker import DeviceWorker
        worker = DeviceWorker(mock=True)
        worker.start()
        try:
            worker.perform_upload(collection().scenes[0].asset)
            before = worker.client.snapshot()
            plan = make_plan(collection(), before, ["add", "add"])
            failed = []
            worker.restore_failed.connect(failed.append)
            with tempfile.TemporaryDirectory() as folder, \
                 patch("fntflshka_us6_clip.device.worker.save", side_effect=PermissionError), \
                 patch.object(DeviceClient, "upload", side_effect=AssertionError("Never write without a backup")):
                worker.restore_collection((plan, Path(folder), Event()))
            self.assertTrue(failed)
            self.assertEqual(tuple(worker.mock_transport.scenes), before.scenes)
        finally:
            worker.stop()


class LibraryGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_offline_import_preview_and_export(self):
        with tempfile.TemporaryDirectory() as folder:
            w = MainWindow(start_workers=False, library_folder=Path(folder) / "library")
            w.show()
            try:
                w.open_library()
                dialog = w.library_dialog
                self.assertTrue(wait_for(lambda: not dialog.io_busy))
                incoming = Path(folder) / "incoming.fclip"
                save(incoming, collection())
                dialog.file_job("import", incoming)
                self.assertTrue(wait_for(lambda: not dialog.io_busy and dialog.collection is not None))
                self.assertFalse(dialog.restore_button.isEnabled())
                self.assertEqual(dialog.collection, collection())
                self.assertFalse(dialog.preview.image.isNull())
                output = Path(folder) / "export.fclip"
                with patch("fntflshka_us6_clip.ui.library_dialog.QFileDialog.getSaveFileName", return_value=(str(output), "")):
                    QTest.mouseClick(dialog.export_button, Qt.LeftButton)
                self.assertTrue(wait_for(lambda: output.exists() and not dialog.io_busy))
                self.assertEqual(load(output), collection())
            finally:
                w.close()
                self.assertTrue(wait_for(lambda: not w.isVisible()))

    def test_mock_backup_restore_is_async_uses_cache_and_saves_pre_restore_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            w = MainWindow(mock=True, library_folder=Path(folder) / "library")
            w.show()
            ticks = []
            timer = QTimer()
            timer.timeout.connect(lambda: ticks.append(True))
            timer.start(10)
            try:
                self.assertTrue(wait_for(lambda: w.snapshot is not None and not w.cache_loading))
                for count, scene in enumerate(collection().scenes, 1):
                    w.request_upload.emit(scene.asset)
                    self.assertTrue(wait_for(lambda: len(w.snapshot.scenes) == count and not w.cache_loading))
                original = w.snapshot.scenes
                w.scenes.setCurrentRow(0)
                w.open_library()
                dialog = w.library_dialog
                self.assertTrue(wait_for(lambda: not dialog.io_busy))
                with patch.object(DeviceClient, "read_scene", side_effect=AssertionError("Backup and restore must reuse RAM")):
                    QTest.mouseClick(dialog.backup_all, Qt.LeftButton)
                    self.assertTrue(wait_for(lambda: dialog.collection is not None and not dialog.io_busy and not dialog.serial_busy))
                    self.assertEqual(len(dialog.collection.scenes), 2)
                    plan_dialog = RestoreDialog(dialog.collection, w.snapshot, dialog)
                    self.assertTrue(plan_dialog.buttons.button(QDialogButtonBox.Cancel).isDefault())
                    self.assertEqual([i.target_id for i in plan_dialog.plan.items], [3, 4])
                    before_ticks = len(ticks)
                    dialog.begin_restore(plan_dialog.plan)
                    self.assertTrue(w.busy)
                    self.assertTrue(wait_for(lambda: not w.busy and not dialog.serial_busy and not dialog.io_busy))
                    self.assertGreater(len(ticks), before_ticks)
                    self.assertEqual(len(w.snapshot.scenes), 4)
                    self.assertEqual(w.snapshot.scenes[:2], tuple(replace(s, active=False) for s in original))
                    backups = list(w.library_folder.glob("before-restore-*.fclip"))
                    self.assertEqual(len(backups), 1)
                    self.assertEqual([s.id for s in load(backups[0]).scenes], [1, 2])
                    self.assertIn("Восстановлено", dialog.status.text())
                    with patch("fntflshka_us6_clip.ui.library_dialog.QInputDialog.getItem", return_value=("ID 2 · Сцена 2", True)):
                        QTest.mouseClick(dialog.backup_one, Qt.LeftButton)
                    self.assertTrue(wait_for(lambda: not dialog.serial_busy and not dialog.io_busy and dialog.collection is not None and len(dialog.collection.scenes) == 1))
                    self.assertEqual(dialog.collection.scenes[0].id, 2)
                    self.assertEqual(len(w.snapshot.scenes), 4)
            finally:
                timer.stop()
                w.close()
                self.assertTrue(wait_for(lambda: not w.isVisible()))
