from pathlib import Path
import logging
from threading import Event
from PIL import Image
from PySide6.QtCore import QThread, QTimer, Signal, Slot, Qt, QSize, QUrl
from PySide6.QtGui import QIcon, QPixmap, QDesktopServices
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QListWidget, QListWidgetItem, QFileDialog, QProgressBar, QMessageBox,
                             QToolButton, QPlainTextEdit, QScrollArea, QFrame, QInputDialog)
from .preview import Preview
from .widgets import PlacementControls
from .storage_bar import StorageBar
from ..scenes.models import estimate_storage, next_scene_id, storage_projection
from ..core.asset import parse_asset
from ..media.rgb565 import decode_rgb565
from ..media.image import SceneSource

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    request_upload = Signal(bytes)
    request_render = Signal(int, object, object)
    request_scene_action = Signal(object)
    request_preview = Signal(object)
    request_replace = Signal(object)
    request_reconnect = Signal()

    def __init__(self, mock=False, log_folder=Path("."), start_workers=True, preferences=None):
        super().__init__()
        self.setWindowTitle("FntflshkaUS6 clip" + (" · ДЕМО" if mock else ""))
        self.resize(1180, 800)
        self.setMinimumSize(980, 650)
        self.setAcceptDrops(True)
        self.log_folder = log_folder
        self.preferences = preferences
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
        body.addLayout(sidebar)
        self.preview = Preview()
        self.preview.drag_started.connect(self.begin_drag)
        self.preview.dragged.connect(self.drag_image)
        self.preview.zoom_requested.connect(self.zoom_image)
        body.addWidget(self.preview, 1)
        self.controls = PlacementControls()
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFrameShape(QFrame.NoFrame)
        controls_scroll.setMinimumWidth(248)
        controls_scroll.setMaximumWidth(270)
        controls_scroll.setWidget(self.controls)
        body.addWidget(controls_scroll)
        layout.addLayout(body, 1)
        self.image_label = QLabel("Перетащи PNG, JPG или WEBP в окно")
        self.image_label.setObjectName("muted")
        self.image_label.setTextFormat(Qt.PlainText)
        image_row = QHBoxLayout()
        image_row.addWidget(self.image_label, 1)
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
        self.controls.changed.connect(self.schedule_render)
        self.snapshot = None
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
            self.image_worker.failed.connect(self.on_render_failed)
            self.image_thread.start()

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
                self.preview_key = None
                self.cache_device = identity
            valid = {(snapshot.port, s.id, s.checksum, s.width, s.height) for s in snapshot.scenes}
            self.scene_previews = {k: v for k, v in self.scene_previews.items() if k in valid}
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
        path, _ = QFileDialog.getOpenFileName(self, "Добавить картинку", self.image_folder, "Картинки (*.png *.jpg *.jpeg *.webp)")
        if path:
            self.import_image(Path(path))

    def selected_scene_id(self):
        item = self.scenes.currentItem()
        return item.data(Qt.UserRole) if item else None

    @property
    def image_source(self):
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
        self.cancel_edit_button.setVisible(self.replace_target is not None)
        self.cancel_edit_button.setEnabled(not self.busy and not self.closing)
        index = next((i for i, s in enumerate(self.snapshot.scenes) if scene and s.id == scene.id), -1) if self.snapshot else -1
        self.up_button.setEnabled(metadata and index > 0)
        self.down_button.setEnabled(metadata and index < len(self.snapshot.scenes) - 1 if self.snapshot else False)
        if scene:
            self.scene_info.setText(
                f"Сцена {scene.id} · {'на экране' if scene.active else 'сохранена'}\n"
                "Preview из памяти приложения; изменение порядка не запускает слайд-шоу.")
        else:
            self.scene_info.setText("Выбери сцену на заколке.")
        self.controls.setEnabled(scene is None and not self.busy and not self.cache_loading)
        self.update_selected_preview(scene)
        self.update_memory()

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
        pixels = decode_rgb565(data[24:], (info["width"], info["height"]))
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
        pixels = decode_rgb565(data[24:], (info["width"], info["height"]))
        canvas = Image.new("RGB", (caps.display_width, caps.display_height), "black")
        canvas.paste(pixels, ((caps.display_width - pixels.width) // 2, (caps.display_height - pixels.height) // 2))
        self.preview.set_image(canvas)
        scene = next(s for s in self.snapshot.scenes if s.id == key[1])
        self.image_label.setText(f"{scene.label} · preview из памяти")

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
        path, _ = QFileDialog.getOpenFileName(self, f"Новая картинка для «{scene.label}»", self.image_folder, "Картинки (*.png *.jpg *.jpeg *.webp)")
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
        self.prepared = None
        self.source_asset = None
        self.source_path = None
        self.replace_target = None
        self.replace_device_id = None
        self.restore_editor_controls()
        self.preview_key = None
        self.preview.clear_image()
        self.on_snapshot(self.snapshot)
        if self.snapshot:
            for row in range(self.scenes.count()):
                if self.scenes.item(row).data(Qt.UserRole) == scene_id:
                    self.scenes.setCurrentRow(row)
                    break
        self.message.setText("Изменения отменены. Сцена на заколке не менялась.")

    def begin_drag(self):
        self.drag_offset = (self.controls.x.value(), self.controls.y.value())

    def drag_image(self, dx, dy):
        if self.preview.interactive:
            self.controls.set_offset(self.drag_offset[0] + dx, self.drag_offset[1] + dy)

    def zoom_image(self, steps, anchor):
        if self.preview.interactive:
            self.controls.zoom_at(steps, anchor)

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
        projection = storage_projection(self.snapshot.capabilities, scene=scene)
        text += f"\nФайл сцены: {projection.reclaim:,} Б. Прогноз свободного места: ≈{projection.projected_free:,} Б.\nТочный результат зависит от A/B-файлов и блоков LittleFS и будет показан после удаления."
        if scene.active:
            text += "\n\nЭта сцена сейчас на экране. После удаления включится встроенная демонстрация."
        reply = QMessageBox.question(self, "Удаление сцены", text,
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            current = self.selected_scene()
            if not current or not self.snapshot or self.snapshot.port != expected_port or current != scene:
                self.message.setText("Список сцен изменился. Выбери сцену и повтори удаление.")
                return
            self.begin_scene_action("delete", scene)

    def begin_scene_action(self, action, scene, extra=None):
        if self.busy or self.closing or self.cache_loading or not self.snapshot:
            return
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

    def import_image(self, path, replacement=None):
        if self.busy or self.cache_loading:
            return
        self.restore_editor_controls()
        self.source_asset = None
        self.replace_target = replacement
        self.replace_device_id = self.snapshot.device_id if replacement and self.snapshot else None
        self.operation_error = None
        self.source_path = Path(path)
        self.source_revision += 1
        self.image_folder = str(self.source_path.resolve().parent)
        self.scenes.clearSelection()
        self.scenes.setCurrentRow(-1)
        self.update_scene_actions()
        self.image_label.setText(self.source_path.name)
        self.message.setText("Готовлю картинку...")
        self.schedule_render()

    def schedule_render(self):
        self.generation += 1
        self.prepared = None
        self.update_memory()
        if self.image_source is not None and not self.debounce.isActive():
            self.debounce.start()

    def dispatch_render(self):
        if self.image_source is not None and not self.render_inflight and self.image_thread:
            self.debounce.stop()
            self.render_inflight = True
            self.render_source_revision = self.source_revision
            self.request_render.emit(self.generation, self.image_source, self.controls.placement())

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
                self.preview.set_image(prepared.preview)
            self.dispatch_render()
            return
        self.prepared = prepared
        if self.selected_scene() is None:
            self.preview.set_image(prepared.preview)
        self.message.setText("Изменения готовы в preview. Нажми «СОХРАНИТЬ СЦЕНУ», чтобы обновить заколку." if self.source_asset is not None
                             else "Картинка готова. Можно заливать :3")
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
        self.preview.set_interactive(editable)
        projection = storage_projection(self.snapshot.capabilities) if self.snapshot else None
        if scene:
            projection = storage_projection(self.snapshot.capabilities, scene=scene)
            self.memory.setText(
                f"Свободно {projection.free:,} / {projection.total:,} Б · серый: занято · бирюзовый: удаляемый файл ≈{projection.reclaim:,} Б\n"
                f"После удаления: ≈{projection.projected_free:,} Б свободно. A/B и служебные блоки уточнятся после удаления.")
        elif self.prepared:
            size = len(self.prepared.asset)
            if self.snapshot:
                estimate = estimate_storage(size, self.snapshot.capabilities)
                free_id = next_scene_id(self.snapshot.scenes)
                projection = storage_projection(self.snapshot.capabilities, upload=size)
                self.memory.setText(
                    f"Свободно {projection.free:,} / {projection.total:,} Б · розовый: запись {size:,} Б · "
                    f"{estimate.reason if free_id or self.replace_target else 'Все 8 мест заняты'}\n"
                    f"После записи: ≈{projection.projected_free:,} Б, нужен запас 8 192 Б. "
                    + ("Старый A/B-файл сохраняется; " if self.replace_target else "")
                    + "точный остаток — после ACK.")
                enabled = estimate.fits and (self.replacement_is_current() if self.replace_target else free_id is not None)
                if self.replace_target and not self.replacement_is_current():
                    self.memory.setText(self.memory.text() + "\nСцена или устройство изменились: отменить изменения и выбрать сцену заново.")
            else:
                self.memory.setText(f"Размер сцены: {size:,} байт · Подключи заколку для проверки памяти.")
        else:
            text = "Готовлю сцену..." if self.image_source is not None else "Добавь картинку или выбери сцену из списка."
            if self.snapshot:
                text += (f"\nСцен: {len(self.snapshot.scenes)}/8 · "
                         f"Свободно: {projection.free:,} / {projection.total:,} Б · серый: занято")
            self.memory.setText(text)
        self.storage_bar.set_projection(projection)
        self.upload_button.setText(f"{'СОХРАНИТЬ' if self.source_asset is not None else 'ЗАМЕНИТЬ'} СЦЕНУ {self.replace_target[0]}" if self.replace_target else "ЗАЛИТЬ В ЗАКОЛКУ")
        self.upload_button.setEnabled(enabled and not self.busy and not self.closing and not self.cache_loading and self.selected_scene() is None)

    def begin_upload(self):
        if not self.prepared or not self.snapshot or self.busy or self.cache_loading or self.closing or self.selected_scene() is not None:
            return
        if self.replace_target:
            expected = (self.replace_target, self.replace_device_id, self.prepared.asset)
            if not self.replacement_is_current():
                self.message.setText("Сцена или устройство изменились. Отмени изменения и выбери сцену заново.")
                return
            reply = QMessageBox.question(self, "Сохранение сцены" if self.source_asset is not None else "Замена картинки",
                                         f"Сохранить изменения сцены {self.replace_target[0]}? Её изображение обновится на заколке. Имя и порядок сохранятся.",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            if not self.prepared or expected != (self.replace_target, self.replace_device_id, self.prepared.asset) or not self.replacement_is_current():
                self.message.setText("Сцена или подключение изменились во время подтверждения. Выбери сцену заново.")
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

    @Slot(object)
    def on_uploaded(self, result):
        edited = self.source_asset is not None
        if edited:
            self.generation += 1
            self.source_revision += 1
            self.source_asset = None
            self.prepared = None
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
            if Path(urls[0].toLocalFile()).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not self.busy and not self.cache_loading and not self.closing and len(urls) == 1 and urls[0].isLocalFile():
            self.import_image(Path(urls[0].toLocalFile()))
            event.acceptProposedAction()

    def closeEvent(self, event):
        self.closing = True
        self.preview_cancel.set()
        self.debounce.stop()
        threads = [t for t in (self.device_thread, self.image_thread) if t and t.isRunning()]
        if threads:
            for thread in threads:
                thread.requestInterruption()
                thread.quit()
            self.status.setText("Закрываю соединение...")
            event.ignore()
            QTimer.singleShot(100, self.close)
        else:
            if self.preferences:
                self.preferences.save(self)
            event.accept()
