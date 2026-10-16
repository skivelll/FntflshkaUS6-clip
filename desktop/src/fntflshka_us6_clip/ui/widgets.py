from PySide6.QtCore import Signal, QSignalBlocker
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSlider, QSpinBox, QCheckBox, QPushButton, QColorDialog, QLayout, QAbstractSpinBox
from PySide6.QtCore import Qt
from ..media.image import Placement


class PlacementControls(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.background = "#000000"
        self.target = (320, 172)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8,8,8,8);layout.setSpacing(6)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        layout.addWidget(QLabel("НАСТРОЙКИ ✦"))
        layout.addWidget(QLabel("Как разместить"))
        self.mode = QComboBox()
        self.mode.addItem("Вписать целиком · FIT", "fit")
        self.mode.addItem("Заполнить экран · FILL", "fill")
        self.mode.currentIndexChanged.connect(self.changed)
        layout.addWidget(self.mode)
        self.scale_label = QLabel("Масштаб · 100%")
        scale_row = QHBoxLayout()
        scale_row.addWidget(self.scale_label)
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(10, 400)
        self.scale_spin.setValue(100)
        self.scale_spin.setSuffix(" %")
        self.scale_spin.setKeyboardTracking(False)
        self.scale_spin.setAccessibleName("Точный масштаб изображения")
        scale_row.addWidget(self.scale_spin)
        layout.addLayout(scale_row)
        self.scale = QSlider(Qt.Horizontal)
        self.scale.setRange(10, 400)
        self.scale.setValue(100)
        self.scale.valueChanged.connect(lambda value: self.scale_label.setText(f"Масштаб · {value}%"))
        self.scale.valueChanged.connect(self.scale_spin.setValue)
        self.scale_spin.valueChanged.connect(self.scale.setValue)
        self.scale.valueChanged.connect(self.changed)
        layout.addWidget(self.scale)
        self.x = QSpinBox()
        self.y = QSpinBox()
        offset_row=QHBoxLayout()
        for name, spin in (("Сдвиг по X", self.x), ("Сдвиг по Y", self.y)):
            column=QVBoxLayout();column.addWidget(QLabel(name))
            spin.setRange(-640, 640)
            spin.setSuffix(" px")
            spin.valueChanged.connect(self.changed)
            column.addWidget(spin);offset_row.addLayout(column)
        layout.addLayout(offset_row)
        self.pixel_art = QCheckBox("Пиксель-арт")
        self.pixel_art.toggled.connect(self.changed)
        layout.addWidget(self.pixel_art)
        self.quality = QWidget()
        quality_layout=QVBoxLayout(self.quality);quality_layout.setContentsMargins(0,0,0,0)
        quality_layout.setSpacing(6)
        quality_layout.addWidget(QLabel('КАЧЕСТВО КАРТИНКИ'))
        self.resampling=QComboBox()
        self.resampling.addItem('Lanczos · детали','lanczos')
        self.resampling.addItem('Bicubic · обычный','bicubic')
        quality_layout.addWidget(self.resampling)
        quality_layout.addWidget(QLabel('Резкость'))
        self.sharpness=QSpinBox();self.sharpness.setRange(0,100);self.sharpness.setValue(25)
        self.sharpness.setSuffix(' %');self.sharpness.setButtonSymbols(QAbstractSpinBox.NoButtons)
        sharpness_row=QHBoxLayout();sharpness_row.addWidget(self.sharpness,1)
        self.sharpness_down=QPushButton('▼');self.sharpness_up=QPushButton('▲')
        for button,title,action in ((self.sharpness_down,'Уменьшить резкость',self.sharpness.stepDown),
                                    (self.sharpness_up,'Увеличить резкость',self.sharpness.stepUp)):
            button.setFixedWidth(44);button.setToolTip(title);button.setAccessibleName(title)
            button.setAutoRepeat(True);button.clicked.connect(action);sharpness_row.addWidget(button)
        quality_layout.addLayout(sharpness_row)
        self.dithering=QCheckBox('Плавные градиенты')
        quality_layout.addWidget(self.dithering)
        self.dithering.setToolTip('Dithering RGB565: уменьшает цветовые ступеньки; может добавить мелкую зернистость.')
        self.reset_quality=QPushButton('Сбросить обработку')
        quality_layout.addWidget(self.reset_quality)
        self.reset_quality.clicked.connect(self.reset_processing)
        for signal in (self.resampling.currentIndexChanged,self.sharpness.valueChanged,self.dithering.toggled):
            signal.connect(self.changed)
        self.pixel_art.toggled.connect(lambda checked:self.quality.setEnabled(not checked))
        layout.addWidget(self.quality)
        layout.addWidget(QLabel("Цвет фона"))
        self.background_button = QPushButton("■ #000000")
        self.background_button.clicked.connect(self.choose_background)
        layout.addWidget(self.background_button)
        reset = QPushButton("Вернуть по центру")
        reset.clicked.connect(self.reset_placement)
        layout.addWidget(reset)
        help_text = QLabel("Тяни картинку в preview мышью.\nКолесо — масштаб у курсора.\nЦвета — итоговый RGB565.")
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
                         self.x.value(), self.y.value(), self.pixel_art.isChecked(), self.background,
                         self.resampling.currentData(),self.sharpness.value(),self.dithering.isChecked())

    def reset_processing(self):
        self.resampling.setCurrentIndex(0);self.sharpness.setValue(25);self.dithering.setChecked(False)
        self.changed.emit()

    def preferences(self):
        placement = self.placement()
        return {key: getattr(placement, key) for key in ("mode", "scale", "x", "y", "pixel_art", "background","resampling","sharpness","dithering")}

    def restore_preferences(self, data):
        if not isinstance(data, dict):
            return
        blockers = [QSignalBlocker(widget) for widget in (self.mode, self.scale, self.x, self.y, self.pixel_art,self.resampling,self.sharpness,self.dithering)]
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
        self.scale_spin.setValue(self.scale.value())
        if data.get('resampling') in ('lanczos','bicubic'):
            self.resampling.setCurrentIndex(self.resampling.findData(data['resampling']))
        if type(data.get('sharpness')) is int and 0<=data['sharpness']<=100:self.sharpness.setValue(data['sharpness'])
        if type(data.get('dithering')) is bool:self.dithering.setChecked(data['dithering'])
        self.quality.setEnabled(not self.pixel_art.isChecked())
        del blockers

    def set_offset(self, x, y):
        blockers = [QSignalBlocker(self.x), QSignalBlocker(self.y)]
        self.x.setValue(round(x))
        self.y.setValue(round(y))
        del blockers
        self.changed.emit()

    def zoom_at(self, steps, anchor):
        old_scale = self.scale.value()
        new_scale = max(10, min(400, round(old_scale * 1.1 ** max(-20, min(20, steps)))))
        if new_scale == old_scale:
            return
        ratio = new_scale / old_scale
        x = anchor[0] - ratio * (anchor[0] - self.x.value())
        y = anchor[1] - ratio * (anchor[1] - self.y.value())
        blockers = [QSignalBlocker(widget) for widget in (self.scale, self.scale_spin, self.x, self.y)]
        self.scale.setValue(new_scale)
        self.scale_spin.setValue(new_scale)
        self.scale_label.setText(f"Масштаб · {new_scale}%")
        self.x.setValue(round(x))
        self.y.setValue(round(y))
        del blockers
        self.changed.emit()
