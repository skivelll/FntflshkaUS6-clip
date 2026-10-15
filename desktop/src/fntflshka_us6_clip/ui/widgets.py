from PySide6.QtCore import Signal, QSignalBlocker
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QComboBox, QSlider, QSpinBox, QCheckBox, QPushButton, QColorDialog
from PySide6.QtCore import Qt
from ..media.image import Placement


class PlacementControls(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setMaximumWidth(245)
        self.setMinimumHeight(550)
        self.background = "#000000"
        self.target = (320, 172)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("НАСТРОЙКИ ✦"))
        layout.addWidget(QLabel("Как разместить"))
        self.mode = QComboBox()
        self.mode.addItem("Вписать целиком · FIT", "fit")
        self.mode.addItem("Заполнить экран · FILL", "fill")
        self.mode.currentIndexChanged.connect(self.changed)
        layout.addWidget(self.mode)
        self.scale_label = QLabel("Масштаб · 100%")
        layout.addWidget(self.scale_label)
        self.scale = QSlider(Qt.Horizontal)
        self.scale.setRange(10, 400)
        self.scale.setValue(100)
        self.scale.valueChanged.connect(lambda value: self.scale_label.setText(f"Масштаб · {value}%"))
        self.scale.valueChanged.connect(self.changed)
        layout.addWidget(self.scale)
        self.x = QSpinBox()
        self.y = QSpinBox()
        for name, spin in (("Сдвиг по X", self.x), ("Сдвиг по Y", self.y)):
            layout.addWidget(QLabel(name))
            spin.setRange(-640, 640)
            spin.setSuffix(" px")
            spin.valueChanged.connect(self.changed)
            layout.addWidget(spin)
        self.pixel_art = QCheckBox("Пиксель-арт")
        self.pixel_art.toggled.connect(self.changed)
        layout.addWidget(self.pixel_art)
        layout.addWidget(QLabel("Цвет фона"))
        self.background_button = QPushButton("■ #000000")
        self.background_button.clicked.connect(self.choose_background)
        layout.addWidget(self.background_button)
        reset = QPushButton("Вернуть по центру")
        reset.clicked.connect(self.reset_placement)
        layout.addWidget(reset)
        help_text = QLabel("В preview — итоговые цвета\nRGB565, как в файле для LCD.")
        help_text.setObjectName("muted")
        layout.addStretch()
        layout.addWidget(help_text)

    def choose_background(self):
        color = QColorDialog.getColor(QColor(self.background), self, "Цвет фона")
        if color.isValid():
            self.background = color.name()
            self.background_button.setText("■ " + self.background)
            self.changed.emit()

    def reset_placement(self):
        self.scale.setValue(100)
        self.x.setValue(0)
        self.y.setValue(0)
        self.changed.emit()

    def placement(self):
        return Placement(*self.target, self.mode.currentData(), self.scale.value(),
                         self.x.value(), self.y.value(), self.pixel_art.isChecked(), self.background)

    def preferences(self):
        placement = self.placement()
        return {key: getattr(placement, key) for key in ("mode", "scale", "x", "y", "pixel_art", "background")}

    def restore_preferences(self, data):
        if not isinstance(data, dict):
            return
        blockers = [QSignalBlocker(widget) for widget in (self.mode, self.scale, self.x, self.y, self.pixel_art)]
        if data.get("mode") in ("fit", "fill"):
            self.mode.setCurrentIndex(self.mode.findData(data["mode"]))
        for key, widget in (("scale", self.scale), ("x", self.x), ("y", self.y)):
            value = data.get(key)
            if type(value) is int and widget.minimum() <= value <= widget.maximum():
                widget.setValue(value)
        if type(data.get("pixel_art")) is bool:
            self.pixel_art.setChecked(data["pixel_art"])
        background = data.get("background")
        if isinstance(background, str) and QColor(background).isValid():
            self.background = QColor(background).name()
        self.background_button.setText("■ " + self.background)
        self.scale_label.setText(f"Масштаб · {self.scale.value()}%")
        del blockers
