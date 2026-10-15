from pathlib import Path
from threading import Event
from PySide6.QtCore import Qt, QThread, QTimer, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QListWidget, QListWidgetItem, QFileDialog, QLineEdit, QProgressBar, QInputDialog)
from .preview import Preview
from .restore_dialog import RestoreDialog
from ..library.worker import LibraryWorker
from ..core.asset import parse_asset


class LibraryDialog(QDialog):
    request_file = Signal(object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.folder = owner.library_folder
        self.setWindowTitle("Библиотека и резервные копии")
        self.setWindowModality(Qt.WindowModal)
        self.resize(1080, 720)
        self.collection = None
        self.previews = []
        self.io_busy = False
        self.serial_busy = False
        self.closing = False
        self.refresh_after = False
        self.pending_path = None
        self.load_after = None
        self.cancel = Event()
        self.notice = None
        layout = QVBoxLayout(self)
        intro = QLabel("Коллекции хранятся на компьютере и доступны без USB. Архив содержит готовый RGB565, названия, порядок и активную сцену.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.title = QLineEdit("Моя коллекция")
        self.title.setMaxLength(120)
        self.title.setPlaceholderText("Название новой резервной копии")
        layout.addWidget(self.title)
        buttons = QHBoxLayout()
        self.backup_all = QPushButton("Сохранить все сцены с платы")
        self.backup_one = QPushButton("Сохранить одну сцену…")
        self.import_button = QPushButton("Импорт архива…")
        self.refresh_button = QPushButton("Обновить")
        for button in (self.backup_all, self.backup_one, self.import_button, self.refresh_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        body = QHBoxLayout()
        self.collections = QListWidget()
        self.collections.setMaximumWidth(260)
        self.scenes = QListWidget()
        self.scenes.setMaximumWidth(220)
        self.preview = Preview()
        body.addWidget(self.collections)
        body.addWidget(self.scenes)
        body.addWidget(self.preview, 1)
        layout.addLayout(body, 1)
        self.details = QLabel()
        self.details.setTextFormat(Qt.PlainText)
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.status = QLabel("Загружаю библиотеку…")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        bottom = QHBoxLayout()
        self.export_button = QPushButton("Экспорт коллекции…")
        self.restore_button = QPushButton("Восстановить на заколку…")
        self.stop_button = QPushButton("Остановить восстановление")
        self.stop_button.hide()
        folder_button = QPushButton("Папка библиотеки")
        folder_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder.resolve()))))
        for button in (self.export_button, self.restore_button, self.stop_button, folder_button):
            bottom.addWidget(button)
        layout.addLayout(bottom)
        self.backup_all.clicked.connect(lambda: self.backup(None))
        self.backup_one.clicked.connect(self.backup_selected)
        self.import_button.clicked.connect(self.import_archive)
        self.export_button.clicked.connect(self.export_archive)
        self.refresh_button.clicked.connect(lambda: self.file_job("list", None))
        self.restore_button.clicked.connect(self.restore)
        self.stop_button.clicked.connect(self.stop_restore)
        self.collections.currentItemChanged.connect(self.select_collection)
        self.scenes.currentRowChanged.connect(self.select_scene)
        self.thread = QThread(self)
        self.worker = LibraryWorker(self.folder)
        self.worker.moveToThread(self.thread)
        self.thread.finished.connect(self.worker.deleteLater)
        self.request_file.connect(self.worker.run)
        self.worker.listed.connect(self.on_listed)
        self.worker.loaded.connect(self.on_loaded)
        self.worker.saved.connect(self.on_saved)
        self.worker.error.connect(self.status.setText)
        self.worker.done.connect(self.on_file_done)
        self.thread.start()
        self.file_job("list", None)

    def update_actions(self):
        idle = not self.io_busy and not self.serial_busy and not self.closing
        device = self.owner.snapshot is not None and not self.owner.cache_loading and not self.owner.busy
        can_export = device and self.owner.snapshot.capabilities.supports_scene_read and bool(self.owner.snapshot.scenes)
        self.backup_all.setEnabled(idle and can_export)
        self.backup_one.setEnabled(idle and can_export)
        self.import_button.setEnabled(idle)
        self.refresh_button.setEnabled(idle)
        self.export_button.setEnabled(idle and self.collection is not None)
        self.restore_button.setEnabled(idle and device and self.collection is not None)
        self.collections.setEnabled(idle)
        self.title.setEnabled(idle)
        self.stop_button.setVisible(self.serial_busy and self.owner.library_operation == "restore")

    def file_job(self, action, payload):
        if self.io_busy or self.closing:
            return
        self.io_busy = True
        self.update_actions()
        self.request_file.emit((action, payload))

    def on_file_done(self):
        self.io_busy = False
        self.update_actions()
        if self.closing:
            return
        if self.refresh_after:
            self.refresh_after = False
            self.file_job("list", None)
        elif self.load_after is not None:
            path, self.load_after = self.load_after, None
            self.file_job("load", path)

    def on_listed(self, rows):
        self.collection = None
        self.previews = []
        self.scenes.clear()
        self.preview.clear_image()
        self.details.clear()
        self.collections.blockSignals(True)
        self.collections.clear()
        for path, title, count in rows:
            item = QListWidgetItem(f"{title}\n{count} сцен")
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))
            self.collections.addItem(item)
            if self.pending_path is not None and Path(path) == self.pending_path:
                self.collections.setCurrentItem(item)
                self.load_after = path
        self.collections.blockSignals(False)
        self.pending_path = None
        self.status.setText(self.notice or f"Коллекций: {len(rows)}. Можно просматривать без платы.")
        if self.load_after is None and rows:
            self.collections.setCurrentRow(0)
            self.load_after = rows[0][0]

    def select_collection(self, item, previous=None):
        self.collection = None
        self.previews = []
        self.scenes.clear()
        self.preview.clear_image()
        self.details.clear()
        if item and not self.io_busy:
            self.file_job("load", Path(item.data(Qt.UserRole)))
        self.update_actions()

    def on_loaded(self, collection, previews):
        self.collection, self.previews = collection, previews
        self.scenes.clear()
        for scene in collection.scenes:
            info = parse_asset(scene.asset)
            self.scenes.addItem(f"{'● ' if scene.id == collection.active_id else ''}{scene.title or f'Сцена {scene.id}'}\nID {scene.id} · {info['width']}×{info['height']}")
        self.scenes.setCurrentRow(0)
        self.details.setText(f"{collection.title} · {collection.created_utc}\n"
                             f"{len(collection.scenes)} сцен · assets {sum(len(s.asset) for s in collection.scenes):,} Б · ACTIVE {collection.active_id}")
        self.update_actions()

    def select_scene(self, row):
        if 0 <= row < len(self.previews):
            if isinstance(self.previews[row], tuple):
                self.preview.set_animation(self.previews[row])
            else:
                self.preview.set_image(self.previews[row])
        else:
            self.preview.clear_image()

    def on_saved(self, path):
        self.notice = "Архив сохранён: " + str(path)
        self.status.setText(self.notice)
        if Path(path).parent == self.folder:
            self.pending_path = Path(path)
            self.refresh_after = True

    def backup(self, selected_id):
        if not self.owner.snapshot or not self.title.text().strip():
            self.status.setText("Подключи плату и задай название коллекции.")
            return
        self.serial_busy = True
        self.notice = None
        self.status.setText("Проверяю snapshot и экспортирую verified RAM assets…")
        self.owner.export_to_library(self.title.text().strip(), selected_id)
        self.update_actions()

    def backup_selected(self):
        snapshot = self.owner.snapshot
        if snapshot is None or not snapshot.scenes:
            self.status.setText("На заколке пока нет сцен.")
            return
        labels = [f"ID {s.id} · {s.label}" for s in snapshot.scenes]
        current = next((i for i, s in enumerate(snapshot.scenes) if s.id == self.owner.selected_scene_id()), 0)
        label, accepted = QInputDialog.getItem(self, "Экспорт одной сцены", "Сцена с заколки:", labels, current, False)
        if accepted:
            if snapshot != self.owner.snapshot:
                self.status.setText("Список сцен или подключение изменились. Выбери сцену заново.")
                return
            self.backup(snapshot.scenes[labels.index(label)].id)

    def receive_collection(self, collection):
        self.serial_busy = False
        self.file_job("save", collection)

    def operation_failed(self, message):
        self.serial_busy = False
        self.status.setText(message)
        self.update_actions()

    def import_archive(self):
        path, _ = QFileDialog.getOpenFileName(self, "Импорт коллекции", "", "Коллекции (*.fclip *.zip)")
        if path:
            self.file_job("import", Path(path))

    def export_archive(self):
        if self.collection is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт коллекции", "collection.fclip", "Коллекции (*.fclip)")
        if path:
            path = Path(path)
            if not path.suffix:
                path = path.with_suffix(".fclip")
            self.file_job("export", (self.collection, path))

    def restore(self):
        if self.collection is None or not self.owner.snapshot or self.serial_busy:
            return
        dialog = RestoreDialog(self.collection, self.owner.snapshot, self)
        if dialog.exec() == QDialog.Accepted and dialog.plan is not None:
            self.begin_restore(dialog.plan)

    def begin_restore(self, plan):
        self.cancel = Event()
        self.serial_busy = True
        self.notice = None
        self.status.setText("Сохраняю резервную копию текущей платы, затем восстанавливаю по плану…")
        self.owner.restore_from_library(plan, self.cancel)
        self.update_actions()

    def stop_restore(self):
        self.cancel.set()
        self.status.setText("Останавливаю. Завершённые шаги сохранятся; текущий шаг потребуется проверить.")

    def restored(self, snapshot):
        self.serial_busy = False
        self.notice = f"Восстановлено и проверено. На плате {len(snapshot.scenes)} сцен, свободно {snapshot.capabilities.storage_free:,} Б. Резервная копия прежнего состояния — в библиотеке."
        self.status.setText(self.notice)
        item = self.collections.currentItem()
        if item:
            self.pending_path = Path(item.data(Qt.UserRole))
        self.refresh_after = False
        self.update_actions()
        self.file_job("list", None)

    def closeEvent(self, event):
        if self.serial_busy:
            self.stop_restore()
            event.ignore()
            return
        self.closing = True
        if self.thread.isRunning():
            self.thread.requestInterruption()
            self.thread.quit()
            event.ignore()
            QTimer.singleShot(100, self.close)
        else:
            event.accept()
