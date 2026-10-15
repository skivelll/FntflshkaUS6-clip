from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QComboBox,
                              QSpinBox, QDialogButtonBox, QWidget, QHBoxLayout, QPushButton, QAbstractSpinBox)


class GifDialog(QDialog):
    def __init__(self, parent, options=("compact", 0, 0, 1)):
        super().__init__(parent)
        self.setWindowTitle("Создать сцену из GIF")
        self.resize(540, 340)
        layout = QVBoxLayout(self)
        self.mode = QComboBox()
        for title, key in (("Компактный 128×128 · до 4 кадров/с", "compact"),
                           ("Компактный 96×96 · до 6 кадров/с", "small"),
                           ("Полный экран 320×172 · до ~3 кадров/с", "full")):
            self.mode.addItem(title, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(options[0])))
        self.first = QSpinBox(); self.first.setRange(1, 300); self.first.setValue(options[1] + 1)
        self.last = QSpinBox(); self.last.setRange(0, 300); self.last.setSpecialValueText("Все кадры"); self.last.setValue(options[2])
        self.step = QSpinBox(); self.step.setRange(1, 20); self.step.setValue(options[3])
        form = QFormLayout()
        form.addRow("Профиль:", self.mode)
        form.addRow("Первый кадр:", self.step_field(self.first, "first"))
        form.addRow("Последний кадр:", self.step_field(self.last, "last"))
        form.addRow("Брать каждый N-й кадр:", self.step_field(self.step, "step"))
        layout.addLayout(form)
        note = QLabel("Лимит 512 KiB: примерно 15 кадров 128×128 или 4 полноэкранных кадра. "
                      "Свободное место может ограничить ещё сильнее. Длинный GIF не обрезается автоматически: "
                      "выбери диапазон или шаг. Темп может стать медленнее исходного; точные кадры и длительность будут в preview. "
                      "Компактный кадр перемещается внутри LCD; в полноэкранном режиме сдвиг и масштаб применяются с обрезкой. "
                      "Полноэкранная анимация медленнее; на LCD может быть видна полосная перерисовка.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Подготовить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self):
        return self.mode.currentData(), self.first.value() - 1, self.last.value(), self.step.value()

    def step_field(self, spin, name):
        spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        widget = QWidget()
        row = QHBoxLayout(widget); row.setContentsMargins(0,0,0,0)
        row.addWidget(spin, 1)
        for text, action, suffix in (("▼",spin.stepDown,"down"),("▲",spin.stepUp,"up")):
            button = QPushButton(text)
            button.setObjectName(f"gif_{name}_{suffix}")
            button.setAccessibleName(f"{name} {suffix}")
            button.setFixedWidth(44); button.setAutoRepeat(True); button.setAutoDefault(False)
            button.clicked.connect(action)
            row.addWidget(button)
        return widget
