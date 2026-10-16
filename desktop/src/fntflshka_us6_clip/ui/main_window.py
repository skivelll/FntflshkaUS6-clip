from pathlib import Path
import logging
from threading import Event
from PIL import Image
from PySide6.QtCore import QThread, QTimer, Signal, Slot, Qt, QSize, QUrl
from PySide6.QtGui import QIcon, QPixmap, QDesktopServices
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QListWidget, QListWidgetItem, QFileDialog, QProgressBar, QMessageBox,
                             QToolButton, QPlainTextEdit, QScrollArea, QFrame, QInputDialog, QCheckBox, QSplitter, QGridLayout, QTabWidget)
from .preview import Preview
from .widgets import PlacementControls
from .storage_bar import StorageBar
from ..scenes.models import estimate_storage, next_scene_id, storage_projection
from ..core.asset import parse_asset, first_frame
from ..media.rgb565 import decode_rgb565
from ..media.image import SceneSource,BlankSource

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    request_upload = Signal(bytes)
    request_render = Signal(int, object, object)
    request_scene_action = Signal(object)
    request_preview = Signal(object)
    request_replace = Signal(object)
    request_reconnect = Signal()
    request_collection = Signal(object)
    request_restore = Signal(object)
    request_playlist = Signal(object)
    request_decode = Signal(object, bytes)
    request_project=Signal(object)

    def __init__(self, mock=False, log_folder=Path("."), start_workers=True, preferences=None, library_folder=None):
        super().__init__()
        self.setWindowTitle("FntflshkaUS6 clip" + (" · ДЕМО" if mock else ""))
        self.resize(1180, 800)
        self.setMinimumSize(980, 650)
        self.setAcceptDrops(True)
        self.log_folder = log_folder
        self.preferences = preferences
        self.library_folder = Path(library_folder) if library_folder is not None else Path(log_folder).parent / "library"
        self.library_dialog = None
        self.library_operation = None
        self.image_folder = ""
        self.connection_message = None
        self.operation_error = None
        self.source_path = None
        self.source_asset = None
        self.editor_preferences = None
        self.edit_scene_label = ""
        self.replace_device_id = None
        self.drag_offset = (0, 0)
        self.prepared = None
        self.busy = False
        self.closing = False
        self.generation = 0
        self.render_inflight = False
        self.source_revision = 0
        self.render_source_revision = None
        self.operation_context = None
        self.preview_key = None
        self.preview_cancel = Event()
        self.scene_previews = {}
        self.scene_icons = {}
        self.scene_animations = {}
        self.gif_options = ("compact", 0, 0, 1)
        self.cache_loading = False
        self.cache_device = None
        self.replace_target = None
        self.image_thread = None
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        brand = QLabel("FntflshkaUS6 clip ✦")
        brand.setObjectName("brand")
        header.addWidget(brand)
        self.library_button = QPushButton("Библиотека…")
        self.library_button.clicked.connect(self.open_library)
        header.addWidget(self.library_button)
        self.playlist_button = QPushButton("Плейлист…")
        self.playlist_button.clicked.connect(self.open_playlist)
        header.addWidget(self.playlist_button)
        header.addStretch()
        self.status = QLabel("● демо-устройство" if mock else "● заколка не найдена")
        self.status.setObjectName("status")
        header.addWidget(self.status)
        self.reconnect_button = QPushButton("Переподключить")
        self.reconnect_button.setToolTip("Повторно найти заколку. Незавершённая загрузка не повторяется.")
        self.reconnect_button.clicked.connect(self.reconnect_device)
        header.addWidget(self.reconnect_button)
        layout.addLayout(header)
        self.cache_label = QLabel()
        self.cache_bar = QProgressBar()
        self.cache_label.hide()
        self.cache_bar.hide()
        layout.addWidget(self.cache_label)
        layout.addWidget(self.cache_bar)
        body = QHBoxLayout()
        sidebar = QVBoxLayout()
        sidebar.addWidget(QLabel("SCENEZ / твои сцены"))
        self.scenes = QListWidget()
        self.scenes.setMaximumWidth(220)
        self.scenes.setIconSize(QSize(72, 40))
        sidebar.addWidget(self.scenes)
        self.scene_info = QLabel("Выбери сцену на заколке.")
        self.scene_info.setWordWrap(True)
        self.scene_info.setMaximumWidth(220)
        sidebar.addWidget(self.scene_info)
        self.activate_button = QPushButton("Показать на заколке")
        self.delete_button = QPushButton("Удалить…")
        self.activate_button.clicked.connect(self.activate_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.scenes.currentItemChanged.connect(self.update_scene_actions)
        sidebar.addWidget(self.activate_button)
        self.edit_button = QPushButton("Редактировать сцену…")
        self.edit_button.setToolTip("Сдвиг и масштаб сохранённого RGB565 из RAM. Исходный PNG и обрезанные части на плате не хранятся.")
        self.edit_button.clicked.connect(self.edit_selected)
        sidebar.addWidget(self.edit_button)
        self.rename_button = QPushButton("Имя…")
        self.rename_button.setToolTip("Переименовать сцену на заколке")
        self.replace_button = QPushButton("Заменить картинку…")
        self.up_button = QPushButton("↑ Выше")
        self.down_button = QPushButton("↓ Ниже")
        self.rename_button.clicked.connect(self.rename_selected)
        self.replace_button.clicked.connect(self.replace_selected)
        self.up_button.clicked.connect(lambda: self.move_selected(-1))
        self.down_button.clicked.connect(lambda: self.move_selected(1))
        metadata_buttons = QHBoxLayout()
        metadata_buttons.addWidget(self.rename_button)
        metadata_buttons.addWidget(self.delete_button)
        sidebar.addLayout(metadata_buttons)
        sidebar.addWidget(self.replace_button)
        order_buttons = QHBoxLayout()
        order_buttons.addWidget(self.up_button)
        order_buttons.addWidget(self.down_button)
        sidebar.addLayout(order_buttons)
        self.add_button = QPushButton("+ добавить")
        self.add_button.clicked.connect(self.choose_image)
        sidebar.addWidget(self.add_button)
        self.gif_button = QPushButton("Настройки GIF…")
        self.gif_button.clicked.connect(self.configure_gif)
        self.gif_button.hide()
        body.addLayout(sidebar)
        self.preview = Preview()
        self.preview.drag_started.connect(self.begin_drag)
        self.preview.dragged.connect(self.drag_image)
        self.preview.zoom_requested.connect(self.zoom_image)
        body.addWidget(self.preview, 1)
        self.controls = PlacementControls()
        from .text_controls import TextControls
        self.text_controls=TextControls()
        self.text_controls.changed.connect(self.on_text_changed)
        self.text_controls.blank.clicked.connect(self.new_text_scene)
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFrameShape(QFrame.NoFrame)
        controls_scroll.setMinimumWidth(248)
        controls_scroll.setMaximumWidth(270)
        controls_scroll.setWidget(self.controls)
        body.addWidget(controls_scroll)
        layout.addLayout(body, 1)
        self.image_label = QLabel("Перетащи PNG, JPG, WEBP или GIF в окно")
        self.image_label.setObjectName("muted")
        self.image_label.setTextFormat(Qt.PlainText)
        image_row = QHBoxLayout()
        image_row.addWidget(self.image_label, 1)
        image_row.addWidget(self.gif_button)
        self.compare_button=QPushButton('До / после…')
        self.compare_button.clicked.connect(self.compare_image)
        image_row.addWidget(self.compare_button)
        self.save_project_button=QPushButton('Сохранить проект…')
        self.save_project_button.clicked.connect(self.save_image_project)
        image_row.addWidget(self.save_project_button)
        self.open_project_button=QPushButton('Открыть проект…')
        self.open_project_button.clicked.connect(self.open_image_project)
        image_row.addWidget(self.open_project_button)
        self.cancel_edit_button = QPushButton("Отменить изменения")
        self.cancel_edit_button.clicked.connect(self.cancel_edit)
        self.cancel_edit_button.hide()
        image_row.addWidget(self.cancel_edit_button)
        layout.addLayout(image_row)
        self.message = QLabel("Заколка не найдена. Подключи её по USB-C." if not mock else "Демо-режим: USB не используется.")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        layout.addWidget(self.message)
        self.memory = QLabel("Добавь картинку, чтобы подготовить сцену.")
        self.memory.setWordWrap(True)
        self.storage_bar = StorageBar()
        layout.addWidget(self.storage_bar)
        layout.addWidget(self.memory)
        self.memory_help=QPushButton('? Почему запись недоступна?')
        self.memory_help.hide();self.memory_help.clicked.connect(self.show_memory_problem)
        layout.addWidget(self.memory_help)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.upload_button = QPushButton("ЗАЛИТЬ В ЗАКОЛКУ")
        self.upload_button.setObjectName("upload")
        self.upload_button.setEnabled(False)
        self.upload_button.clicked.connect(self.begin_upload)
        layout.addWidget(self.upload_button)
        details_toggle = QToolButton()
        details_toggle.setText("▸ Устройство и журнал")
        details_toggle.setCheckable(True)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(150)
        self.details.hide()
        details_toggle.toggled.connect(self.details.setVisible)
        layout.addWidget(details_toggle)
        self.logs_button = QPushButton("Открыть папку журнала")
        self.logs_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_folder.resolve()))))
        self.logs_button.hide()
        details_toggle.toggled.connect(self.logs_button.setVisible)
        layout.addWidget(self.logs_button)
        layout.addWidget(self.details)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(33)
        self.debounce.timeout.connect(self.dispatch_render)
        self.text_animation_pending=False
        self.text_idle=QTimer(self);self.text_idle.setSingleShot(True);self.text_idle.setInterval(300)
        self.text_idle.timeout.connect(self.finish_text_animation)
        self.controls.changed.connect(self.schedule_render)
        self.snapshot = None
        from .notifications import Notifications
        self.notifications=Notifications(self,self.problem_context)
        self.problem_banner=QPushButton('')
        self.problem_banner.setStyleSheet('color: #FF9AA7; text-align: left;')
        self.problem_banner.hide()
        self.problem_banner.clicked.connect(self.notifications.show_last)
        layout.addWidget(self.problem_banner)
        self.notifications.changed.connect(self.on_problem)
        self.notifications.resolved.connect(self.on_problem_resolved)
        self.error_sound=QCheckBox('Звук ошибок')
        self.error_sound.setChecked(False)
        self.error_sound.toggled.connect(self.set_error_sound)
        layout.addWidget(self.error_sound)
        self.arrange_workspace(layout,body,image_row,controls_scroll,details_toggle)
        if self.preferences:
            self.preferences.restore(self)
        self.update_scene_actions()
        self.device_thread = None
        if start_workers:
            from ..device.worker import DeviceWorker
            self.device_thread = QThread(self)
            self.device_worker = DeviceWorker(mock)
            self.device_worker.moveToThread(self.device_thread)
            self.device_thread.started.connect(self.device_worker.start)
            self.device_thread.finished.connect(self.device_worker.deleteLater)
            self.device_thread.finished.connect(self.device_worker.stop)
            self.device_worker.status.connect(self.status.setText)
            self.device_worker.snapshot_ready.connect(self.on_snapshot)
            self.device_worker.error.connect(self.on_device_error)
            self.device_worker.failure.connect(self.notifications.present)
            self.device_worker.recovered.connect(self.notifications.resolve)
            self.device_worker.progress.connect(self.on_progress)
            self.device_worker.upload_done.connect(self.on_uploaded)
            self.device_worker.upload_failed.connect(self.on_upload_failed)
            self.device_worker.scene_done.connect(self.on_scene_done)
            self.device_worker.scene_failed.connect(self.on_scene_failed)
            self.request_scene_action.connect(self.device_worker.change_scene)
            self.request_preview.connect(self.device_worker.read_preview)
            self.device_worker.preview_ready.connect(self.on_scene_preview)
            self.device_worker.preview_failed.connect(self.on_preview_failed)
            self.device_worker.preview_progress.connect(self.on_preview_progress)
            self.request_upload.connect(self.device_worker.upload)
            self.request_replace.connect(self.device_worker.replace_image)
            self.request_reconnect.connect(self.device_worker.reconnect)
            self.request_collection.connect(self.device_worker.export_collection)
            self.request_restore.connect(self.device_worker.restore_collection)
            self.request_playlist.connect(self.device_worker.playlist_action)
            self.device_worker.collection_ready.connect(self.on_collection_ready)
            self.device_worker.collection_failed.connect(self.on_collection_failed)
            self.device_worker.restore_done.connect(self.on_collection_restored)
            self.device_worker.restore_failed.connect(self.on_collection_failed)
            self.device_worker.cache_state.connect(self.on_cache_state)
            self.device_worker.cache_progress.connect(self.on_cache_progress)
            self.cache_label.setText("Подключаюсь и загружаю сцены...")
            self.on_cache_state(True)
            self.device_thread.start()
            from ..media.worker import ImageWorker
            self.image_thread = QThread(self)
            self.image_worker = ImageWorker()
            self.image_worker.moveToThread(self.image_thread)
            self.image_thread.finished.connect(self.image_worker.deleteLater)
            self.request_render.connect(self.image_worker.render)
            self.image_worker.ready.connect(self.on_rendered)
            self.request_decode.connect(self.image_worker.decode_preview)
            self.image_worker.decoded.connect(self.on_animation_preview)
            self.image_worker.failed.connect(self.on_render_failed)
            self.image_worker.failure.connect(self.on_media_problem)
            self.request_project.connect(self.image_worker.project)
            self.image_worker.project_loaded.connect(self.on_project_loaded)
            self.image_worker.project_saved.connect(self.on_project_saved)
            self.image_thread.start()

    def arrange_workspace(self,layout,old_body,old_image_row,controls_scroll,details_toggle):
        self.columns=QSplitter(Qt.Horizontal)
        self.columns.setChildrenCollapsible(False)
        self.scene_panel=QFrame();self.scene_panel.setObjectName('panel')
        left=QVBoxLayout(self.scene_panel)
        left.addWidget(QLabel('ТВОИ СЦЕНЫ'))
        self.scenes.setMaximumWidth(16777215)
        self.scenes.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scenes.setTextElideMode(Qt.ElideRight)
        left.addWidget(self.scenes,1);left.addWidget(self.add_button)
        self.new_text_button=QPushButton('+ текстовая сцена')
        self.new_text_button.clicked.connect(self.new_text_scene);left.addWidget(self.new_text_button)
        self.scene_panel.setMinimumWidth(220)

        self.editor_panel=QWidget();center=QVBoxLayout(self.editor_panel)
        center.setContentsMargins(0,0,0,0)
        center.addWidget(self.preview,1)
        self.scene_actions_panel=QFrame();self.scene_actions_panel.setObjectName('panel')
        actions=QVBoxLayout(self.scene_actions_panel);actions.setSpacing(6)
        self.scene_info.setMaximumWidth(16777215)
        self.image_label.setWordWrap(True)
        actions.addWidget(self.image_label);actions.addWidget(self.scene_info)
        grid=QGridLayout()
        self.activate_button.setText('Показать на LCD')
        self.edit_button.setText('Редактировать…')
        self.replace_button.setText('Заменить…')
        buttons=(self.activate_button,self.edit_button,self.replace_button,self.rename_button,
                 self.delete_button,self.up_button,self.down_button,self.cancel_edit_button)
        for i,button in enumerate(buttons):grid.addWidget(button,i//4,i%4)
        actions.addLayout(grid)
        for widget in (self.message,self.storage_bar,self.memory,self.memory_help,self.progress,self.upload_button):
            actions.addWidget(widget)
        journal=QHBoxLayout();journal.addWidget(details_toggle);journal.addStretch();journal.addWidget(self.error_sound)
        actions.addLayout(journal)
        for widget in (self.logs_button,self.details,self.problem_banner):actions.addWidget(widget)
        center.addWidget(self.scene_actions_panel)
        self.editor_panel.setMinimumWidth(500)

        self.settings_panel=QFrame();self.settings_panel.setObjectName('panel')
        right=QVBoxLayout(self.settings_panel)
        controls_scroll.setMinimumWidth(0);controls_scroll.setMaximumWidth(16777215)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.settings_tabs=QTabWidget()
        self.settings_tabs.addTab(controls_scroll,'Изображение')
        text_scroll=QScrollArea();text_scroll.setWidgetResizable(True);text_scroll.setFrameShape(QFrame.NoFrame)
        text_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);text_scroll.setWidget(self.text_controls)
        self.settings_tabs.addTab(text_scroll,'Текст и движение')
        right.addWidget(self.settings_tabs,1)
        tools=QHBoxLayout();tools.addWidget(self.compare_button);tools.addWidget(self.gif_button)
        right.addLayout(tools)
        projects=QHBoxLayout();projects.addWidget(self.save_project_button);projects.addWidget(self.open_project_button)
        right.addLayout(projects)
        self.settings_panel.setMinimumWidth(380)
        for panel in (self.scene_panel,self.editor_panel,self.settings_panel):self.columns.addWidget(panel)
        self.columns.setStretchFactor(0,0);self.columns.setStretchFactor(1,1);self.columns.setStretchFactor(2,0)
        self.columns.setSizes([280,1080,440])
        layout.removeItem(old_body);layout.removeItem(old_image_row)
        def discard_unused(items):
            for i in range(items.count()):
                item=items.itemAt(i)
                if item.widget():item.widget().hide();item.widget().deleteLater()
                elif item.layout():discard_unused(item.layout())
        discard_unused(old_body);discard_unused(old_image_row)
        old_body.deleteLater();old_image_row.deleteLater()
        layout.addWidget(self.columns,1)

    def problem_context(self):
        from dataclasses import asdict
        from PySide6 import __version__
        import sys,json
        build='Source checkout; portable build metadata unavailable'
        if getattr(sys,'frozen',False):
            try:build=json.loads((Path(sys.executable).parent/'build-info.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):build='Portable build metadata unavailable'
        return {'ui_snapshot':asdict(self.snapshot) if self.snapshot else None,
                'pyside_version':__version__,
                'build':build,
                'source_path':str(self.source_path) if self.source_path else None,
                'selected_scene':self.selected_scene_id(),'replacement':self.replace_target,
                'busy':self.busy,'cache_loading':self.cache_loading,
                'log_folder':str(self.log_folder.resolve())}

    @Slot(object)
    def on_problem(self,value):
        self.problem_banner.setText('! '+value.title+' · ? Подробнее / скопировать отчёт')
        self.problem_banner.show()

    @Slot(str)
    def on_problem_resolved(self,operation):
        value=self.notifications.last
        if operation=='connection' and value and value.operation=='connection':
            self.problem_banner.hide()

    @Slot(bool)
    def set_error_sound(self,value):
        self.notifications.sound=value

    def notify_error(self,error,operation='validation',state=None):
        from ..problems import problem
        self.notifications.recovered(operation)
        self.notifications.present(problem(error,operation,state=state))

    @Slot()
    def show_memory_problem(self):
        self.notify_error(ValueError(self.memory.text()),'validation')

    @Slot(int,object)
    def on_media_problem(self,generation,value):
        if generation==-2:
            self.centralWidget().setEnabled(True)
            self.notifications.present(value)
            return
        if generation>=0 and generation!=self.generation:return
        if generation<0:
            request=value.context.get('request')
            if request!=(self.cache_device,self.preview_key):return
        self.notifications.present(value)

    @Slot(object)
    def on_snapshot(self, snapshot):
        selected = self.selected_scene_id()
        if self.snapshot and snapshot and self.snapshot.port != snapshot.port:
            selected = None
        self.snapshot = snapshot
        if snapshot:
            identity = snapshot.device_id or snapshot.port
            if identity != self.cache_device:
                self.scene_previews.clear()
                self.scene_icons.clear()
                self.scene_animations.clear()
                self.preview_key = None
                self.cache_device = identity
            valid = {(snapshot.port, s.id, s.checksum, s.width, s.height) for s in snapshot.scenes}
            self.scene_previews = {k: v for k, v in self.scene_previews.items() if k in valid}
            self.scene_animations = {k: v for k, v in self.scene_animations.items() if k in valid}
        if snapshot and (self.message.text() == self.connection_message or self.message.text() in (
                "Заколка не найдена. Подключи её по USB-C.",
                "Связь с заколкой потеряна. Подключи её по USB-C.")):
            self.message.setText("Заколка подключена. Добавь картинку :3")
            self.connection_message = None
        scroll = self.scenes.verticalScrollBar().value()
        self.scenes.blockSignals(True)
        self.scenes.clear()
        if snapshot:
            caps = snapshot.capabilities
            self.preview.target = (caps.display_width, caps.display_height)
            target_changed = self.controls.target != self.preview.target
            self.controls.target = self.preview.target
            if target_changed and self.image_source is not None:
                self.schedule_render()
            for scene in snapshot.scenes:
                item = QListWidgetItem(f"{'● ' if scene.active else ''}{scene.label}\nID {scene.id} · {scene.width}×{scene.height}")
                item.setData(Qt.UserRole, scene.id)
                item.setToolTip(f"{scene.label}\nID {scene.id} · {scene.width}×{scene.height}")
                self.scenes.addItem(item)
                key = (snapshot.port, scene.id, scene.checksum, scene.width, scene.height)
                if key in self.scene_previews:
                    self.set_scene_thumbnail(item, self.scene_previews[key])
                if scene.id == selected:
                    self.scenes.setCurrentItem(item)
            if self.image_source is not None:
                self.scenes.addItem("✎ " + self.source_name + (" · изменения" if self.replace_target else " · новая"))
            self.details.setPlainText(
                f"Port: {snapshot.port}\nFirmware: {'.'.join(map(str, snapshot.firmware_version))}\n"
                f"Protocol: {caps.protocol_version} / Asset: {caps.asset_version}\n"
                f"Display: {caps.display_width}×{caps.display_height} / Pixel format: {caps.pixel_format}\n"
                f"LittleFS total/free: {caps.storage_total}/{caps.storage_free}\n"
                f"Max asset/chunk: {caps.max_asset_size}/{caps.max_upload_chunk}\n"
                f"Render buffer: {caps.render_buffer_size}\nЖурнал: {self.log_folder}")
        else:
            self.details.setPlainText(f"Заколка не подключена.\nЖурнал: {self.log_folder}")
        self.scenes.blockSignals(False)
        self.scenes.verticalScrollBar().setValue(scroll)
        self.update_scene_actions()
        self.preview.update()
        self.update_memory()

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Добавить картинку", self.image_folder, "Картинки (*.png *.jpg *.jpeg *.webp *.gif)")
        if path:
            self.import_image(Path(path))

    def selected_scene_id(self):
        item = self.scenes.currentItem()
        return item.data(Qt.UserRole) if item else None

    @property
    def image_source(self):
        if self.source_asset is None and self.source_path and self.source_path.suffix.lower() == ".gif":
            from ..media.animation import GifSource
            return GifSource(self.source_path, *self.gif_options,
                             optimize=not self.snapshot or self.snapshot.capabilities.supports_compression)
        return self.source_asset if self.source_asset is not None else self.source_path

    @property
    def source_name(self):
        return self.edit_scene_label if self.source_asset is not None else self.source_path.name if self.source_path else "Новая картинка"

    def replacement_is_current(self):
        return bool(self.replace_target and self.snapshot and self.replace_target[2] == self.snapshot.port
                    and self.replace_device_id == self.snapshot.device_id
                    and any(s.id == self.replace_target[0] and s.checksum == self.replace_target[1] for s in self.snapshot.scenes))

    def selected_scene(self):
        scene_id = self.selected_scene_id()
        return next((s for s in self.snapshot.scenes if s.id == scene_id), None) if self.snapshot else None

    def update_scene_actions(self, *args):
        self.gif_button.setVisible(bool(self.source_path and self.source_path.suffix.lower() == ".gif"
                                        and self.source_asset is None and self.selected_scene() is None))
        self.gif_button.setEnabled(not self.busy and not self.cache_loading and not self.closing)
        self.library_button.setEnabled(not self.busy and not self.closing)
        self.playlist_button.setEnabled(bool(self.snapshot and self.snapshot.capabilities.supports_playlist)
                                        and not self.busy and not self.cache_loading and not self.closing)
        self.reconnect_button.setEnabled(not self.busy and not self.closing and not self.cache_loading)
        scene = self.selected_scene()
        available = scene is not None and not self.busy and not self.closing and not self.cache_loading
        self.activate_button.setEnabled(available and not scene.active if scene else False)
        self.delete_button.setEnabled(available)
        metadata = bool(available and self.snapshot.capabilities.supports_scene_metadata)
        self.rename_button.setEnabled(metadata)
        self.replace_button.setEnabled(available)
        key = (self.snapshot.port, scene.id, scene.checksum, scene.width, scene.height) if scene else None
        self.edit_button.setEnabled(available and key in self.scene_previews)
        self.cancel_edit_button.setVisible(self.replace_target is not None or bool(self.text_controls.layers))
        self.cancel_edit_button.setEnabled(not self.busy and not self.closing)
        index = next((i for i, s in enumerate(self.snapshot.scenes) if scene and s.id == scene.id), -1) if self.snapshot else -1
        self.up_button.setEnabled(metadata and index > 0)
        self.down_button.setEnabled(metadata and index < len(self.snapshot.scenes) - 1 if self.snapshot else False)
        if scene:
            self.scene_info.setText(
                f"Сцена {scene.id} · {'на экране' if scene.active else 'сохранена'}\n"
                f"{scene.width}×{scene.height} · {'GIF' if scene.type == 2 else 'картинка'} · preview в RAM")
        else:
            self.scene_info.setText("Выбери сцену на заколке.")
        self.controls.setEnabled(scene is None and not self.busy and not self.cache_loading)
        self.update_selected_preview(scene)
        self.update_memory()
        if self.library_dialog is not None:
            self.library_dialog.update_actions()

    def open_library(self):
        if self.library_dialog is None:
            from .library_dialog import LibraryDialog
            dialog = LibraryDialog(self)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            self.library_dialog = dialog
            dialog.destroyed.connect(lambda *_: setattr(self, "library_dialog", None) if self.library_dialog is dialog else None)
        self.library_dialog.show()
        self.library_dialog.raise_()

    def open_playlist(self):
        if not self.snapshot or not self.snapshot.capabilities.supports_playlist or self.busy:
            return
        from .playlist_dialog import PlaylistDialog
        dialog = PlaylistDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def export_to_library(self, title, selected_id):
        if self.busy or self.cache_loading or self.snapshot is None or self.device_thread is None:
            self.library_dialog.operation_failed("Заколка пока недоступна. Дождись подключения и загрузки кэша.")
            return
        self.library_operation = "export"
        self.busy = True
        self.add_button.setEnabled(False)
        self.scenes.setEnabled(False)
        self.update_scene_actions()
        self.request_collection.emit((self.snapshot, title, selected_id))

    def restore_from_library(self, plan, cancel):
        if self.busy or self.cache_loading or self.snapshot is None or self.device_thread is None:
            self.library_dialog.operation_failed("Заколка пока недоступна. Создай план после подключения.")
            return
        self.library_operation = "restore"
        self.busy = True
        self.add_button.setEnabled(False)
        self.scenes.setEnabled(False)
        self.update_scene_actions()
        self.request_restore.emit((plan, self.library_folder, cancel))

    @Slot(object)
    def on_collection_ready(self, collection):
        self.library_operation = None
        self.finish_scene_action()
        if self.library_dialog is not None:
            self.library_dialog.receive_collection(collection)

    @Slot(str)
    def on_collection_failed(self, message):
        self.library_operation = None
        self.finish_scene_action()
        if self.library_dialog is not None:
            self.library_dialog.operation_failed(message)

    @Slot(object)
    def on_collection_restored(self, snapshot):
        self.notifications.recovered('restore')
        self.library_operation = None
        self.finish_scene_action()
        self.message.setText("Коллекция восстановлена и проверена по плану.")
        if self.library_dialog is not None:
            self.library_dialog.restored(snapshot)

    def update_selected_preview(self, scene):
        key = (self.snapshot.port, scene.id, scene.checksum, scene.width, scene.height) if scene else None
        if key == self.preview_key:
            return
        self.preview_cancel.set()
        self.preview_cancel = Event()
        self.preview_key = key
        if key is None:
            if self.prepared:
                self.preview.set_image(self.prepared.preview)
                self.image_label.setText(self.source_name)
            else:
                self.preview.clear_image()
            return
        self.preview.clear_image()
        self.image_label.setText(f"{scene.label} · загружается в память..." if self.snapshot.capabilities.supports_scene_read
                                 else f"{scene.label} · для preview нужна firmware 1.1 или новее")
        if key in self.scene_previews:
            self.on_scene_preview(key, self.scene_previews[key])
        # Selection is always local. Missing assets are loaded by startup/change prefetch.

    def set_scene_thumbnail(self, item, data):
        if data in self.scene_icons:
            item.setIcon(self.scene_icons[data])
            return
        info = parse_asset(data)
        pixels = decode_rgb565(first_frame(data), (info["width"], info["height"]))
        from PySide6.QtGui import QImage
        image = QImage(pixels.tobytes(), pixels.width, pixels.height, pixels.width * 3, QImage.Format_RGB888).copy()
        icon = QIcon(QPixmap.fromImage(image).scaled(72, 40, Qt.KeepAspectRatio, Qt.FastTransformation))
        if len(self.scene_icons) >= 8:
            self.scene_icons.pop(next(iter(self.scene_icons)))
        self.scene_icons[data] = icon
        item.setIcon(icon)

    @Slot(object, bytes)
    def on_scene_preview(self, key, data):
        if not self.snapshot or not any((self.snapshot.port, s.id, s.checksum, s.width, s.height) == key for s in self.snapshot.scenes):
            return
        self.scene_previews[key] = data
        for index in range(self.scenes.count()):
            item = self.scenes.item(index)
            if item.data(Qt.UserRole) == key[1]:
                self.set_scene_thumbnail(item, data)
        if key != self.preview_key:
            return
        info = parse_asset(data)
        caps = self.snapshot.capabilities
        if info.get("type") == 2:
            if key in self.scene_animations:
                self.preview.set_animation(self.scene_animations[key])
            else:
                self.request_decode.emit((self.cache_device, key), data)
            self.image_label.setText(f"{next(s for s in self.snapshot.scenes if s.id == key[1]).label} · GIF из памяти")
            return
        pixels = decode_rgb565(data[24:], (info["width"], info["height"]))
        canvas = Image.new("RGB", (caps.display_width, caps.display_height), "black")
        canvas.paste(pixels, ((caps.display_width - pixels.width) // 2, (caps.display_height - pixels.height) // 2))
        self.preview.set_image(canvas)
        scene = next(s for s in self.snapshot.scenes if s.id == key[1])
        self.image_label.setText(f"{scene.label} · preview из памяти")

    @Slot(object, object)
    def on_animation_preview(self, request, frames):
        identity, key = request
        if identity != self.cache_device or key not in self.scene_previews:
            return
        # Compressed assets stay cached in RAM for the entire connection. Bound
        # expanded previews separately: otherwise many tiny RLE files could
        # retain gigabytes of decoded frames after visiting each scene.
        self.scene_animations.pop(key,None)
        def pixels(value):
            image=value.frames[0][0]
            return image.width*image.height*len(value)
        incoming=pixels(frames)
        while self.scene_animations and sum(pixels(v) for v in self.scene_animations.values())+incoming>32_000_000:
            self.scene_animations.pop(next(iter(self.scene_animations)))
        self.scene_animations[key] = frames
        if key == self.preview_key:
            self.preview.set_animation(frames)

    @Slot(bool)
    def on_cache_state(self, loading):
        self.cache_loading = loading
        if loading:
            self.cache_bar.setValue(0)
        self.cache_label.setVisible(loading)
        self.cache_bar.setVisible(loading)
        self.scenes.setEnabled(not loading and not self.busy)
        self.add_button.setEnabled(not loading and not self.busy)
        self.update_scene_actions()

    @Slot(str)
    def on_device_error(self, message):
        if self.busy:
            self.operation_error = message
        self.connection_message = None if self.operation_error else message
        self.message.setText(self.operation_error + "\n" + message if self.operation_error and self.operation_error != message else message)

    def reconnect_device(self):
        if self.busy or self.cache_loading or self.closing:
            return
        self.notifications.recovered('connection')
        self.cache_label.setText("Переподключаю заколку...")
        self.on_cache_state(True)
        self.request_reconnect.emit()

    @Slot(int, int, int, int)
    def on_cache_progress(self, completed, count, done, total):
        fraction = done / total if total else 0
        self.cache_bar.setValue(int(100 * (completed + fraction) / max(1, count)))
        self.cache_label.setText(f"Загружаю сцены в память: {min(completed + 1, count)}/{count} · {self.cache_bar.value()}%")

    def rename_selected(self):
        scene = self.selected_scene()
        if not scene or self.busy or self.cache_loading:
            return
        title, accepted = QInputDialog.getText(self, "Название сцены", "До 48 байт UTF-8 (обычно до 24 русских букв):", text=scene.title)
        if accepted:
            self.begin_scene_action("rename", scene, title)

    def move_selected(self, direction):
        scene = self.selected_scene()
        if not scene or self.busy or self.cache_loading:
            return
        scenes = list(self.snapshot.scenes)
        index = next(i for i, s in enumerate(scenes) if s.id == scene.id)
        target = index + direction
        if not 0 <= target < len(scenes):
            return
        scenes[index], scenes[target] = scenes[target], scenes[index]
        self.begin_scene_action("reorder", scene, [(s.id, s.checksum) for s in scenes])

    def replace_selected(self):
        scene = self.selected_scene()
        if not scene or self.busy or self.cache_loading:
            return
        target = (scene.id, scene.checksum, self.snapshot.port)
        path, _ = QFileDialog.getOpenFileName(self, f"Новая картинка для «{scene.label}»", self.image_folder, "Картинки (*.png *.jpg *.jpeg *.webp *.gif)")
        if path:
            self.import_image(Path(path), replacement=target)

    def edit_selected(self):
        scene = self.selected_scene()
        if not scene or self.busy or self.cache_loading or self.closing:
            return
        key = (self.snapshot.port, scene.id, scene.checksum, scene.width, scene.height)
        data = self.scene_previews.get(key)
        if data is None:
            self.message.setText("Дождись загрузки этой сцены в память приложения.")
            return
        self.restore_editor_controls()
        self.editor_preferences = self.controls.preferences()
        self.controls.restore_preferences({"mode": "fit", "scale": 100, "x": 0, "y": 0,
                                           "pixel_art": True, "background": "#000000"})
        self.source_path = None
        self.source_asset = SceneSource(data, self.controls.target)
        self.text_controls.set_document()
        self.source_revision += 1
        self.edit_scene_label = scene.label
        self.replace_target = (scene.id, scene.checksum, self.snapshot.port)
        self.replace_device_id = self.snapshot.device_id
        self.operation_error = None
        self.scenes.setCurrentRow(-1)
        self.on_snapshot(self.snapshot)
        self.schedule_render()
        self.image_label.setText(self.source_name + " · редактирование сохранённой картинки")
        self.message.setText("Редактируем RGB565 с платы. Обрезанные части исходного PNG восстановить нельзя. До сохранения LCD не меняется.")

    def restore_editor_controls(self):
        if self.editor_preferences is not None:
            self.controls.restore_preferences(self.editor_preferences)
            self.editor_preferences = None

    def cancel_edit(self):
        if self.busy or self.closing:
            return
        scene_id = self.replace_target[0] if self.replace_target else None
        self.generation += 1
        self.source_revision += 1
        self.debounce.stop()
        self.text_idle.stop();self.text_animation_pending=False
        self.prepared = None
        self.source_asset = None
        self.source_path = None
        self.replace_target = None
        self.replace_device_id = None
        self.restore_editor_controls()
        self.text_controls.set_document()
        self.preview_key = None
        self.preview.clear_image()
        self.on_snapshot(self.snapshot)
        if self.snapshot:
            for row in range(self.scenes.count()):
                if self.scenes.item(row).data(Qt.UserRole) == scene_id:
                    self.scenes.setCurrentRow(row)
                    break
        self.message.setText("Изменения отменены. Сцена на заколке не менялась.")

    def render_placement(self):
        from dataclasses import replace
        return replace(self.controls.placement(),text_layers=self.text_controls.layers,
            text_motion=self.text_controls.motion.currentData(),text_speed=self.text_controls.speed.value(),
            text_pause=self.text_controls.pause.value())

    def new_text_scene(self,*_,preserve_layers=False):
        if self.busy or self.cache_loading or self.closing:return
        from ..core.asset import encode_asset
        if not preserve_layers:self.cancel_edit()
        self.source_path=None
        self.source_asset=BlankSource(encode_asset(Image.new('RGB',self.controls.target)),self.controls.target)
        self.edit_scene_label='Новая текстовая сцена'
        self.replace_target=None;self.replace_device_id=None;self.source_revision+=1
        self.scenes.clearSelection();self.scenes.setCurrentRow(-1)
        self.settings_tabs.setCurrentIndex(1)
        self.update_scene_actions();self.image_label.setText('Текст на выбранном фоне · новая сцена')
        self.message.setText('Добавь текстовый слой справа. До загрузки на заколке ничего не меняется.')
        self.schedule_render()

    def on_text_changed(self):
        if self.busy or self.cache_loading or self.closing:return
        if self.image_source is None and self.text_controls.layers:self.new_text_scene(preserve_layers=True)
        else:self.schedule_render()

    def begin_drag(self):
        self.drag_text=self.settings_tabs.currentIndex()==1 and self.text_controls.selected() is not None
        controls=self.text_controls if self.drag_text else self.controls
        self.drag_offset = (controls.x.value(), controls.y.value())

    def drag_image(self, dx, dy):
        if self.preview.interactive:
            controls=self.text_controls if getattr(self,'drag_text',False) else self.controls
            controls.set_offset(self.drag_offset[0] + dx, self.drag_offset[1] + dy)

    def zoom_image(self, steps, anchor):
        if self.preview.interactive:
            if self.settings_tabs.currentIndex()==1 and self.text_controls.selected():
                spin=self.text_controls.size;spin.setValue(round(spin.value()*1.1**steps))
            else:self.controls.zoom_at(steps, anchor)

    @Slot(object, str)
    def on_preview_failed(self, key, message):
        if key == self.preview_key:
            self.image_label.setText(f"Сцена {key[1]} · preview недоступен")
            self.message.setText(message)

    @Slot(object, int, int)
    def on_preview_progress(self, key, done, total):
        if key == self.preview_key:
            self.image_label.setText(f"Сцена {key[1]} · читаю preview {done * 100 // total}%")

    def activate_selected(self):
        scene = self.selected_scene()
        if scene and not scene.active:
            self.begin_scene_action("activate", scene)

    def delete_selected(self):
        scene = self.selected_scene()
        if not scene or self.busy or self.closing:
            return
        expected_port = self.snapshot.port
        text = f"Удалить сцену {scene.id} с заколки? Это действие нельзя отменить."
        key = (self.snapshot.port, scene.id, scene.checksum, scene.width, scene.height)
        cached = self.scene_previews.get(key)
        projection = storage_projection(self.snapshot.capabilities, scene=scene, asset_size=len(cached) if cached else None)
        text += f"\nФайл сцены: {projection.reclaim:,} Б. Прогноз свободного места: ≈{projection.projected_free:,} Б.\nТочный результат зависит от A/B-файлов и блоков LittleFS и будет показан после удаления."
        if scene.active:
            text += "\n\nЭта сцена сейчас на экране. После удаления включится встроенная демонстрация."
        reply = QMessageBox.question(self, "Удаление сцены", text,
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            current = self.selected_scene()
            if not current or not self.snapshot or self.snapshot.port != expected_port or current != scene:
                self.message.setText("Список сцен изменился. Выбери сцену и повтори удаление.")
                self.notify_error(ValueError(self.message.text()),state='Удаление не начиналось.')
                return
            self.begin_scene_action("delete", scene)

    def begin_scene_action(self, action, scene, extra=None):
        if self.busy or self.closing or self.cache_loading or not self.snapshot:
            return
        self.notifications.recovered('scene')
        self.operation_context = (action, scene.id)
        self.operation_error = None
        self.preview_cancel.set()
        if self.preview_key not in self.scene_previews:
            self.preview_key = None
        self.busy = True
        self.controls.setEnabled(False)
        self.add_button.setEnabled(False)
        self.scenes.setEnabled(False)
        self.update_scene_actions()
        self.update_memory()
        self.message.setText({"activate": "Включаю сцену...", "delete": "Удаляю сцену...",
                              "rename": "Сохраняю название...", "reorder": "Сохраняю порядок..."}[action])
        request = (action, scene.id, scene.checksum, self.snapshot.port)
        self.request_scene_action.emit(request + (extra,) if extra is not None else request)

    @Slot(object)
    def on_scene_done(self, result):
        self.notifications.recovered('scene')
        action, scene_id, before, after = result
        self.finish_scene_action()
        if action in ("rename", "reorder"):
            self.message.setText("Название сохранено на заколке." if action == "rename" else "Порядок сцен сохранён на заколке.")
            return
        self.message.setText(f"Сцена {scene_id} включена на заколке." if action == "activate" else
                             f"Сцена {scene_id} удалена. Свободно {after.capabilities.storage_free:,} байт "
                             f"(изменение: {after.capabilities.storage_free - before.capabilities.storage_free:+,} Б)." +
                             (" Включена встроенная демонстрация." if not any(s.active for s in after.scenes) else ""))

    @Slot()
    def on_scene_failed(self):
        self.finish_scene_action()

    def finish_scene_action(self):
        self.operation_context = None
        self.busy = False
        self.controls.setEnabled(True)
        self.add_button.setEnabled(True)
        self.scenes.setEnabled(True)
        self.update_memory()
        self.update_scene_actions()

    def import_image(self, path, replacement=None, gif_options=None):
        if self.busy or self.cache_loading:
            return
        if Path(path).suffix.lower() == ".gif":
            if gif_options is None:
                from .gif_dialog import GifDialog
                from ..media.image import Placement
                import_placement = (Placement(width=self.controls.target[0],height=self.controls.target[1],**self.editor_preferences)
                                    if self.editor_preferences is not None else self.controls.placement())
                old_scene = next((s for s in self.snapshot.scenes if replacement and s.id == replacement[0]), None) if self.snapshot else None
                dialog = GifDialog(self, self.gif_options, path=path,
                                   placement=import_placement,
                                   caps=self.snapshot.capabilities if self.snapshot else None,
                                   old_size=old_scene.size if old_scene else 0)
                if not dialog.exec():
                    return
                gif_options = dialog.options()
            self.gif_options = gif_options
        self.restore_editor_controls()
        self.source_asset = None
        self.text_controls.set_document()
        self.notifications.recovered('image')
        self.replace_target = replacement
        self.replace_device_id = self.snapshot.device_id if replacement and self.snapshot else None
        self.operation_error = None
        self.source_path = Path(path)
        self.gif_button.setVisible(self.source_path.suffix.lower() == ".gif")
        self.source_revision += 1
        self.image_folder = str(self.source_path.resolve().parent)
        self.scenes.clearSelection()
        self.scenes.setCurrentRow(-1)
        self.update_scene_actions()
        self.image_label.setText(self.source_path.name)
        self.message.setText("Готовлю картинку...")
        self.schedule_render()

    def save_image_project(self):
        if not self.save_project_button.isEnabled() or not self.image_thread:return
        dialog=QFileDialog(self,'Сохранить изображение и обработку',self.image_folder,'Проект изображения (*.fclipimage)')
        dialog.setAcceptMode(QFileDialog.AcceptSave);dialog.setDefaultSuffix('fclipimage')
        if dialog.exec():
            path=dialog.selectedFiles()[0]
            self.notifications.recovered('image')
            self.centralWidget().setEnabled(False)
            self.request_project.emit(('save',Path(path),self.image_source,self.render_placement()))

    def open_image_project(self):
        if not self.open_project_button.isEnabled() or not self.image_thread:return
        path,_=QFileDialog.getOpenFileName(self,'Открыть проект изображения',self.image_folder,'Проект изображения (*.fclipimage)')
        if path:
            self.notifications.recovered('image')
            self.centralWidget().setEnabled(False)
            self.request_project.emit(('load',Path(path),None,None))

    @Slot(str)
    def on_project_saved(self,path):
        self.centralWidget().setEnabled(True)
        self.message.setText('Проект сохранён: исходник и настройки можно открыть снова. На заколке ничего не изменилось.')

    @Slot(str,object)
    def on_project_loaded(self,path,placement):
        from dataclasses import asdict
        self.centralWidget().setEnabled(True)
        if self.closing:return
        self.restore_editor_controls()
        self.source_asset=None;self.source_path=Path(path)
        self.replace_target=None;self.replace_device_id=None
        self.source_revision+=1
        self.controls.restore_preferences(asdict(placement))
        self.text_controls.set_document(placement.text_layers,placement.text_motion,placement.text_speed,placement.text_pause)
        self.scenes.clearSelection();self.scenes.setCurrentRow(-1)
        self.update_scene_actions();self.gif_button.hide()
        self.image_label.setText(self.source_path.name+' · локальный исходник')
        self.schedule_render()

    def compare_image(self):
        if not self.prepared or self.prepared.before is None:return
        from .comparison import ComparisonDialog
        dialog=ComparisonDialog(self.prepared.before,self.prepared.preview,self)
        dialog.exec()

    def configure_gif(self):
        if self.source_path and self.source_path.suffix.lower() == ".gif" and not self.busy:
            from .gif_dialog import GifDialog
            old_scene = next((s for s in self.snapshot.scenes if self.replace_target and s.id == self.replace_target[0]), None) if self.snapshot else None
            dialog = GifDialog(self, self.gif_options, path=self.source_path,
                               placement=self.controls.placement(),
                               caps=self.snapshot.capabilities if self.snapshot else None,
                               old_size=old_scene.size if old_scene else 0)
            if dialog.exec():
                self.notifications.recovered('image')
                self.gif_options = dialog.options()
                self.source_revision += 1
                self.schedule_render()

    def schedule_render(self):
        self.generation += 1
        self.prepared = None
        self.text_animation_pending=bool(self.text_controls.layers and self.text_controls.motion.currentData()!='static')
        self.text_idle.stop()
        if self.text_animation_pending:self.text_idle.start()
        self.update_memory()
        if self.image_source is not None and not self.debounce.isActive():
            self.debounce.start()

    def dispatch_render(self):
        if self.image_source is not None and not self.render_inflight and self.image_thread:
            self.debounce.stop()
            self.render_inflight = True
            self.render_source_revision = self.source_revision
            placement=self.render_placement()
            if self.text_animation_pending:
                from ..media.image import EditorRenderRequest
                placement=EditorRenderRequest(placement,preview_only=True)
            self.request_render.emit(self.generation, self.image_source, placement)

    def finish_text_animation(self):
        if self.closing or self.image_source is None or not self.text_animation_pending:return
        if self.preview._drag_origin is not None:
            self.text_idle.start();return
        self.text_animation_pending=False;self.generation+=1;self.prepared=None
        self.debounce.stop();self.update_memory();self.dispatch_render()

    @Slot(int, object)
    def on_rendered(self, generation, prepared):
        self.render_inflight = False
        if generation != self.generation:
            # Continuous gestures produce newer placements before the previous
            # RGB565 frame finishes. Display those intermediate frames only for
            # this source; they must never become uploadable prepared assets.
            if (self.render_source_revision == self.source_revision and self.image_source is not None
                    and self.selected_scene() is None and not self.closing and not self.busy
                    and prepared.preview.size == self.controls.target):
                self.preview.set_image(prepared.frames[prepared.animation_start][0] if prepared.frames else prepared.preview)
            self.dispatch_render()
            return
        if prepared.preview_only:
            if self.selected_scene() is None:self.preview.set_image(prepared.preview)
            self.message.setText('Размещаем текст. Движение подготовится после остановки; загрузка пока недоступна.')
            self.update_memory()
            return
        self.prepared = prepared
        self.notifications.recovered('image')
        if self.selected_scene() is None:
            if prepared.frames:
                self.preview.set_animation(prepared.frames,prepared.animation_start)
            else:
                self.preview.set_image(prepared.preview)
        self.message.setText("Изменения готовы в preview. Нажми «СОХРАНИТЬ СЦЕНУ», чтобы обновить заколку." if self.source_asset is not None and self.replace_target
                             else "Картинка готова. Можно заливать :3")
        if prepared.summary:
            self.message.setText(prepared.summary)
        self.update_memory()

    @Slot(int, str)
    def on_render_failed(self, generation, message):
        self.render_inflight = False
        if generation != self.generation:
            self.dispatch_render()
        else:
            self.message.setText(message)
            self.prepared = None
            self.update_memory()

    def update_memory(self):
        enabled = False
        scene = self.selected_scene()
        editable = self.image_source is not None and scene is None and not self.busy and not self.closing and not self.cache_loading
        animated=(bool(self.source_path and self.source_path.suffix.lower()=='.gif') or
                  bool(self.source_asset and parse_asset(self.source_asset.asset).get('type')==2))
        self.controls.quality.setVisible(not animated)
        self.text_controls.setEnabled(not animated and scene is None and not self.busy and not self.cache_loading and not self.closing)
        self.new_text_button.setEnabled(not self.busy and not self.cache_loading and not self.closing)
        self.settings_tabs.setTabText(1,'Текст · только картинки' if animated else 'Текст · режим просмотра' if scene else 'Текст и движение')
        self.settings_tabs.setTabToolTip(1,'Для сохранённой картинки сначала нажми «Редактировать…». Текст поверх GIF пока не поддерживается; можно создать новую текстовую сцену слева.')
        self.compare_button.setEnabled(bool(editable and self.prepared and self.prepared.before))
        self.save_project_button.setEnabled(bool(editable and not animated))
        self.open_project_button.setEnabled(not self.busy and not self.cache_loading and not self.closing)
        self.preview.set_interactive(editable)
        projection = storage_projection(self.snapshot.capabilities) if self.snapshot else None
        if scene:
            key = (self.snapshot.port, scene.id, scene.checksum, scene.width, scene.height)
            cached = self.scene_previews.get(key)
            projection = storage_projection(self.snapshot.capabilities, scene=scene, asset_size=len(cached) if cached else None)
            self.memory.setText(
                f"Свободно {projection.free:,} / {projection.total:,} Б · серый: занято · бирюзовый: удаляемый файл ≈{projection.reclaim:,} Б\n"
                f"После удаления: ≈{projection.projected_free:,} Б свободно. A/B и служебные блоки уточнятся после удаления.")
        elif self.prepared:
            size = len(self.prepared.asset)
            if self.snapshot:
                old_scene = next((s for s in self.snapshot.scenes if self.replace_target and s.id == self.replace_target[0]), None)
                old_size = old_scene.size if old_scene else 0
                estimate = estimate_storage(size, self.snapshot.capabilities, old_size)
                free_id = next_scene_id(self.snapshot.scenes, self.snapshot.capabilities)
                projection = storage_projection(self.snapshot.capabilities, upload=size, old_size=old_size)
                self.memory.setText(
                    f"Свободно {projection.free:,} / {projection.total:,} Б · розовый: запись {size:,} Б · "
                    f"{estimate.reason if free_id or self.replace_target else 'Достигнут бюджет записей сцен'}\n"
                    f"После записи: ≈{projection.projected_free:,} Б, служебный запас {self.snapshot.capabilities.service_reserve:,} Б. "
                    + ("Старый A/B-файл сохраняется; " if self.replace_target else "")
                    + "точный остаток — после ACK.")
                enabled = estimate.fits and (self.replacement_is_current() if self.replace_target else free_id is not None)
                if parse_asset(self.prepared.asset).get("type") == 2 and not self.snapshot.capabilities.supports_animation:
                    enabled = False
                    self.memory.setText(self.memory.text() + "\nДля GIF нужна firmware 1.6.")
                if parse_asset(self.prepared.asset).get('encoding')==1 and not self.snapshot.capabilities.supports_compression:
                    enabled=False
                    self.memory.setText(self.memory.text()+'\nДля сжатой анимации текста нужна firmware 1.8.')
                if self.replace_target and not self.replacement_is_current():
                    self.memory.setText(self.memory.text() + "\nСцена или устройство изменились: отменить изменения и выбрать сцену заново.")
            else:
                self.memory.setText(f"Размер сцены: {size:,} байт · Подключи заколку для проверки памяти.")
        else:
            text = "Готовлю сцену..." if self.image_source is not None else "Добавь картинку или выбери сцену из списка."
            if self.snapshot:
                text += (f"\nСцен: {len(self.snapshot.scenes)} · "
                         f"Свободно: {projection.free:,} / {projection.total:,} Б · серый: занято")
            self.memory.setText(text)
        if self.snapshot and self.snapshot.capabilities.supports_library_budget:
            caps = self.snapshot.capabilities
            self.memory.setText(self.memory.text() +
                f"\nБиблиотека: {caps.library_used:,} / {caps.library_limit:,} Б · физически свободно {caps.storage_free:,} Б."
                + ("\nСтарая библиотека превышает новый бюджет. Сцены сохранены; удали лишнее или уменьши файл." if caps.library_used > caps.library_limit else ""))
        self.storage_bar.set_projection(projection)
        self.upload_button.setText(f"{'СОХРАНИТЬ' if self.source_asset is not None else 'ЗАМЕНИТЬ'} СЦЕНУ {self.replace_target[0]}" if self.replace_target else "ЗАЛИТЬ В ЗАКОЛКУ")
        self.upload_button.setEnabled(enabled and not self.busy and not self.closing and not self.cache_loading and self.selected_scene() is None)
        self.memory_help.setVisible(bool(self.prepared and self.snapshot and not enabled and not self.busy and not self.cache_loading and self.selected_scene() is None))

    def begin_upload(self):
        if not self.prepared or not self.snapshot or self.busy or self.cache_loading or self.closing or self.selected_scene() is not None:
            return
        self.notifications.recovered('upload')
        if self.replace_target:
            expected = (self.replace_target, self.replace_device_id, self.prepared.asset)
            if not self.replacement_is_current():
                self.message.setText("Сцена или устройство изменились. Отмени изменения и выбери сцену заново.")
                self.notify_error(ValueError(self.message.text()),state='Запись не начиналась.')
                return
            reply = QMessageBox.question(self, "Сохранение сцены" if self.source_asset is not None else "Замена картинки",
                                         f"Сохранить изменения сцены {self.replace_target[0]}? Её изображение обновится на заколке. Имя и порядок сохранятся.",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            if not self.prepared or expected != (self.replace_target, self.replace_device_id, self.prepared.asset) or not self.replacement_is_current():
                self.message.setText("Сцена или подключение изменились во время подтверждения. Выбери сцену заново.")
                self.notify_error(ValueError(self.message.text()),state='Запись не начиналась.')
                return
        self.operation_error = None
        self.busy = True
        self.scenes.setEnabled(False)
        self.update_scene_actions()
        self.controls.setEnabled(False)
        self.add_button.setEnabled(False)
        self.progress.setValue(0)
        self.message.setText("Загружаю сцену...")
        self.update_memory()
        if self.replace_target:
            self.request_replace.emit((self.prepared.asset, *self.replace_target, self.replace_device_id))
        else:
            self.request_upload.emit(self.prepared.asset)

    @Slot(int, int)
    def on_progress(self, done, total):
        self.progress.setValue(done * 100 // total)
        self.progress.setFormat(f"{done:,} / {total:,} байт")
        if self.library_dialog is not None and self.library_operation == "restore":
            self.library_dialog.progress.setValue(done * 100 // total)
            self.library_dialog.progress.setFormat(f"{done:,} / {total:,} Б")

    @Slot(object)
    def on_uploaded(self, result):
        self.notifications.recovered('upload')
        edited = self.source_asset is not None and self.replace_target is not None
        if edited:
            self.generation += 1
            self.source_revision += 1
            self.source_asset = None
            self.prepared = None
            self.text_controls.set_document()
            self.restore_editor_controls()
        self.replace_target = None
        self.replace_device_id = None
        self.busy = False
        self.scenes.setEnabled(True)
        self.update_scene_actions()
        self.controls.setEnabled(True)
        self.add_button.setEnabled(True)
        self.progress.setValue(100)
        self.message.setText(f"Готово :3 Сцена {result[0]} сохранена и включена на заколке.")
        if edited:
            self.on_snapshot(result[2])
            for row in range(self.scenes.count()):
                if self.scenes.item(row).data(Qt.UserRole) == result[0]:
                    self.scenes.setCurrentRow(row)
                    break
        self.update_scene_actions()
        self.update_memory()

    @Slot()
    def on_upload_failed(self):
        self.busy = False
        self.scenes.setEnabled(True)
        self.update_scene_actions()
        self.controls.setEnabled(True)
        self.add_button.setEnabled(True)
        self.update_memory()

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if not self.busy and not self.cache_loading and not self.closing and len(urls) == 1 and urls[0].isLocalFile():
            if Path(urls[0].toLocalFile()).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not self.busy and not self.cache_loading and not self.closing and len(urls) == 1 and urls[0].isLocalFile():
            self.import_image(Path(urls[0].toLocalFile()))
            event.acceptProposedAction()

    def closeEvent(self, event):
        self.notifications.shutdown()
        if self.library_dialog is not None and self.library_dialog.isVisible():
            self.library_dialog.close()
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        self.closing = True
        self.preview_cancel.set()
        self.debounce.stop()
        self.text_idle.stop()
        threads = [t for t in (self.device_thread, self.image_thread) if t and t.isRunning()]
        if threads:
            for thread in threads:
                thread.requestInterruption()
                thread.quit()
            self.status.setText("Закрываю соединение...")
            event.ignore()
            QTimer.singleShot(100, self.close)
        else:
            if self.preferences and not getattr(self,'preferences_warning_shown',False):
                if self.preferences.save(self) is False:
                    self.preferences_warning_shown=True
                    self.notifications.closing=False
                    self.notify_error(OSError(f'QSettings status: {self.preferences.store.status()}'),'settings',
                                      state='Не удалось сохранить настройки окна и редактора на компьютере. Сцены на заколке эта операция не меняла. Приложение закроется после подтверждения.')
                    self.notifications.dialog.finished.connect(self.close)
                    event.ignore()
                    return
            event.accept()
