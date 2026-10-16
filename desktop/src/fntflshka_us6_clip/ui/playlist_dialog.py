from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QSpinBox, QCheckBox, QMessageBox, QWidget, QAbstractSpinBox, QComboBox)
from ..device.playlist import Playlist


class PlaylistDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.baseline = owner.snapshot
        self.pending = False
        self.setWindowTitle("Автономный плейлист")
        self.setWindowModality(Qt.WindowModal)
        self.resize(560, 560)
        layout = QVBoxLayout(self)
        note = QLabel("Выбери сцены галочками и порядок стрелками. Заколка переключает их сама, даже при закрытом приложении. После RESET начинает с первой доступной сцены.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.scenes = QListWidget()
        self.scenes.setIconSize(QSize(96, 52))
        layout.addWidget(self.scenes)
        row = QHBoxLayout()
        for text, delta in (("↑ Выше", -1), ("↓ Ниже", 1)):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, d=delta: self.move(d))
            row.addWidget(button)
        layout.addLayout(row)
        self.enabled = QCheckBox("Включить автономное переключение")
        layout.addWidget(self.enabled)
        self.interval_row = QWidget()
        settings = QVBoxLayout(self.interval_row)
        settings.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Интервал, секунд:"))
        self.interval = QSpinBox()
        self.interval.setRange(1, 86400)
        self.interval.setValue(5)
        self.interval.setButtonSymbols(QAbstractSpinBox.NoButtons)
        row.addWidget(self.interval)
        self.interval_down = QPushButton("▼")
        self.interval_up = QPushButton("▲")
        for button, tooltip, action in (
                (self.interval_down, "Уменьшить интервал на секунду", self.interval.stepDown),
                (self.interval_up, "Увеличить интервал на секунду", self.interval.stepUp)):
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.setFixedWidth(44)
            button.setAutoRepeat(True)
            button.setAutoDefault(False)
            button.clicked.connect(action)
            row.addWidget(button)
        settings.addLayout(row)
        effect_row = QHBoxLayout()
        effect_row.addWidget(QLabel("Переход:"))
        self.transition = QComboBox()
        self.transition.addItems(("Обычная смена", "Раскрытие слева направо", "Раскрытие справа налево",
                                  "Раскрытие сверху вниз", "Раскрытие снизу вверх", "Из центра"))
        self.transition.setEnabled(self.baseline.capabilities.supports_transitions)
        self.transition.setToolTip("Для выбора переходов нужна firmware 1.4. Обычная смена скрывает отрисовку короткой тёмной паузой.")
        effect_row.addWidget(self.transition, 1)
        settings.addLayout(effect_row)
        layout.addWidget(self.interval_row)
        self.interval_row.hide()
        self.enabled.toggled.connect(self.interval_row.setVisible)
        self.interval.valueChanged.connect(self.update_interval_buttons)
        self.update_interval_buttons()
        self.message = QLabel("Читаю настройки…")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        layout.addWidget(self.message)
        note = QLabel("Ручной показ, upload и удаление останавливают плейлист. Удалённые сцены пропускаются; замена картинки сохраняет её ID. Библиотека .fclip хранит сцены, но не настройки плейлиста.")
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.save_button = QPushButton("Сохранить на заколке")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.save_button)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        owner.device_worker.playlist_ready.connect(self.loaded)
        owner.device_worker.playlist_failed.connect(self.failed)
        self.pending = True
        self.owner.notifications.recovered('playlist')
        owner.request_playlist.emit((None, self.baseline))

    def move(self, delta):
        if self.pending:
            return
        index = self.scenes.currentRow()
        target = index + delta
        if index >= 0 and 0 <= target < self.scenes.count():
            item = self.scenes.takeItem(index)
            self.scenes.insertItem(target, item)
            self.scenes.setCurrentRow(target)

    def loaded(self, value):
        self.owner.notifications.recovered('playlist')
        was_save = self.pending and self.save_button.isEnabled()
        self.pending = False
        self.scenes.clear()
        current = {s.id: s for s in self.baseline.scenes}
        ids = list(value.ids) + [s.id for s in self.baseline.scenes if s.id not in value.ids]
        for scene_id in ids:
            scene = current.get(scene_id)
            if scene is None:
                continue
            item = QListWidgetItem(f"ID {scene_id} · {scene.title or 'Без названия'}")
            item.setData(Qt.UserRole, scene_id)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if scene_id in value.ids else Qt.Unchecked)
            key = (self.baseline.port, scene.id, scene.checksum, scene.width, scene.height)
            data = self.owner.scene_previews.get(key)
            if data is not None:
                self.owner.set_scene_thumbnail(item, data)
            self.scenes.addItem(item)
        self.interval.setValue(value.interval_ms // 1000)
        self.transition.setCurrentIndex(value.transition)
        self.enabled.setChecked(value.enabled)
        self.save_button.setEnabled(True)
        self.close_button.setEnabled(True)
        self.message.setText("Настройки подтверждены заколкой." if was_save else "Настройки прочитаны. Изменения применяются после сохранения.")

    def failed(self, message):
        self.pending = False
        self.close_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self.message.setText(message)

    def update_interval_buttons(self):
        self.interval_down.setEnabled(self.interval.value() > self.interval.minimum())
        self.interval_up.setEnabled(self.interval.value() < self.interval.maximum())

    def save(self):
        if self.pending:
            return
        ids = tuple(self.scenes.item(i).data(Qt.UserRole) for i in range(self.scenes.count())
                    if self.scenes.item(i).checkState() == Qt.Checked)
        value = Playlist(self.enabled.isChecked(), self.interval.value() * 1000, ids,
                         self.transition.currentIndex())
        try:
            value.encode_extended() if self.baseline.capabilities.supports_library_budget else value.encode()
        except ValueError as error:
            self.message.setText(str(error))
            self.owner.notify_error(error,'validation')
            return
        if QMessageBox.question(self, "Сохранить плейлист?", "Записать порядок, интервал и режим воспроизведения на заколку? Сами картинки не изменятся.",
                                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        self.pending = True
        self.close_button.setEnabled(False)
        self.owner.notifications.recovered('playlist')
        self.message.setText("Сохраняю…")
        self.owner.request_playlist.emit((value, self.baseline))

    def reject(self):
        if not self.pending:
            super().reject()
