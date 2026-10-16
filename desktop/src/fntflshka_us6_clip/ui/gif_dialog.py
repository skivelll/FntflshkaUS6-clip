from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QComboBox,
                              QSpinBox, QDialogButtonBox, QWidget, QHBoxLayout, QPushButton, QAbstractSpinBox, QProgressBar)
from PySide6.QtCore import QTimer, Slot
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image
from ..core.capacity import MAX_FRAMES, LIBRARY_MAX, allocated_size
from ..media.animation import GifSource, load_gif, prepare_gif
from ..media.image import Placement
from dataclasses import replace


def optimized_size(path, options, placement):
    request = GifSource(Path(path),*options,optimize=True)
    loaded = load_gif(path,request)
    return prepare_gif(loaded,replace(request,first=0,last=0,step=1),placement,measure_only=True)


def gif_frame_count(path):
    if Path(path).stat().st_size > 32 * 1024 * 1024:
        raise ValueError("GIF больше 32 MiB.")
    with Image.open(path) as image:
        if image.format != "GIF" or image.width * image.height > 4_000_000:
            raise ValueError("Нужен GIF до 4 мегапикселей.")
        count = image.n_frames
        if not 2 <= count <= MAX_FRAMES:
            raise ValueError(f"В исходном GIF {count} кадров; поддерживается от 2 до {MAX_FRAMES}.")
        return count


class GifDialog(QDialog):
    def __init__(self, parent, options=("compact", 0, 0, 1), *, path=None, caps=None, old_size=0, frame_count=None, placement=None):
        super().__init__(parent)
        self.setWindowTitle("Создать сцену из GIF")
        self.resize(640, 580)
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)
        self.mode = QComboBox()
        for title, key in (("Компактный 128×128 · до 4 кадров/с", "compact"),
                           ("Компактный 96×96 · до 6 кадров/с", "small"),
                           ("Полный экран 320×172 · до ~3 кадров/с", "full")):
            self.mode.addItem(title, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(options[0])))
        self.first = QSpinBox(); self.first.setRange(1, MAX_FRAMES); self.first.setValue(options[1] + 1)
        self.last = QSpinBox(); self.last.setRange(0, MAX_FRAMES); self.last.setSpecialValueText("Все кадры"); self.last.setValue(options[2])
        self.step = QSpinBox(); self.step.setRange(1, MAX_FRAMES); self.step.setValue(options[3])
        for control in (self.mode,self.first,self.last,self.step):
            control.setMinimumHeight(40)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        form.addRow("Профиль:", self.mode)
        form.addRow("Первый кадр:", self.step_field(self.first, "first"))
        form.addRow("Последний кадр:", self.step_field(self.last, "last"))
        form.addRow("Брать каждый N-й кадр:", self.step_field(self.step, "step"))
        layout.addLayout(form)
        note = QLabel("До 820 KiB: до 25 кадров 128×128 или 7 полноэкранных кадров. "
                      "Свободное место может ограничить ещё сильнее. Длинный GIF не обрезается автоматически: "
                      "выбери диапазон или шаг. Темп может стать медленнее исходного; точные кадры и длительность будут в preview. "
                      "Компактный кадр перемещается внутри LCD; в полноэкранном режиме сдвиг и масштаб применяются с обрезкой. "
                      "Полноэкранная анимация медленнее; на LCD может быть видна полосная перерисовка.")
        note.setWordWrap(True)
        if not caps or caps.supports_compression:
            note.setText('До 820 KiB после оптимизации. Приложение автоматически выбирает полные кадры, RLE и изменённые области без дополнительной потери качества. Число кадров зависит от картинки. Диапазон и шаг не обрезаются автоматически. Ниже показан фактический размер готовой сцены. Темп может быть медленнее исходного; preview показывает точные RGB565 пиксели.')
        layout.addWidget(note)
        self.caps, self.old_size, self.frame_count = caps, old_size, frame_count
        self.memory = QLabel()
        self.memory.setWordWrap(True)
        self.memory.setAccessibleName("Расчёт памяти GIF")
        layout.addWidget(self.memory)
        self.problem_button=QPushButton('? Почему не получается?')
        self.problem_button.hide();self.problem_button.clicked.connect(self.show_problem)
        layout.addWidget(self.problem_button)
        self.current_problem=None;self.problem_dialog=None
        self.memory_bar = QProgressBar()
        self.memory_bar.setRange(0, 1000)
        layout.addWidget(self.memory_bar)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.prepare_button = buttons.button(QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("Подготовить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for spin in (self.first, self.last, self.step):
            spin.valueChanged.connect(self.update_memory)
        self.mode.currentIndexChanged.connect(self.update_memory)
        self.scan_error = None
        self.gif_path, self.placement = path, placement or Placement()
        self.analysis = None
        self.analysis_key = None
        self.analysis_result = None
        self.executor = ThreadPoolExecutor(max_workers=1) if path is not None else None
        self.analysis_timer = QTimer(self)
        self.analysis_timer.timeout.connect(self.analyze_finished)
        self.analysis_timer.start(100)
        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self.scan_finished)
        if path is not None and frame_count is None:
            self.scan = self.executor.submit(gif_frame_count, path)
            self.scan_timer.start(50)
        self.finished.connect(self.stop_scan)
        self.update_memory()

    @Slot(int)
    def stop_scan(self, *_):
        self.scan_timer.stop()
        self.analysis_timer.stop()
        if self.executor:
            self.executor.shutdown(wait=False, cancel_futures=True)

    @Slot()
    def scan_finished(self):
        if not self.scan.done():
            return
        self.scan_timer.stop()
        try:
            self.frame_count = self.scan.result()
        except Exception as error:
            self.scan_error = str(error)
            self.set_problem(error)
            self.show_problem()
        self.update_memory()

    @Slot()
    def analyze_finished(self):
        if self.frame_count is None or not self.gif_path or (self.caps and not self.caps.supports_compression):
            return
        key = self.options()
        if self.analysis is not None:
            if not self.analysis.done():
                return
            try:
                result = self.analysis.result()
            except Exception as error:
                result = str(error)
                if self.analysis_key==self.options():self.set_problem(error)
            self.analysis_result = (self.analysis_key,result)
            self.analysis = None
            self.update_memory()
        if self.analysis_result is None or self.analysis_result[0] != key:
            self.analysis_key = key
            self.analysis = self.executor.submit(optimized_size,self.gif_path,key,self.placement)

    @Slot()
    @Slot(int)
    def update_memory(self, *_):
        if self.frame_count is None:
            self.memory.setText(self.scan_error or "Считаю кадры и размер будущей сцены…")
            self.prepare_button.setEnabled(False)
            return
        end = min(self.last.value() or self.frame_count, self.frame_count)
        count = len(range(self.first.value() - 1, end, self.step.value()))
        width, height = {"compact": (128,128), "small": (96,96), "full": (320,172)}[self.mode.currentData()]
        size = 40 + count * (12 + width * height * 2)
        raw_size = size
        optimized = bool(self.gif_path and (not self.caps or self.caps.supports_compression))
        estimate = None
        if optimized:
            if not self.analysis_result or self.analysis_result[0] != self.options():
                self.memory.setText(f"Выбрано {count} из {self.frame_count} кадров. Без сжатия: {raw_size/1024:.1f} KiB.\nРассчитываю фактический размер после оптимизации…")
                self.memory.setStyleSheet('')
                self.prepare_button.setEnabled(False)
                self.memory_bar.setFormat("Рассчитываю…")
                return
            estimate = self.analysis_result[1]
            if isinstance(estimate,str):
                self.memory.setText(estimate)
                self.prepare_button.setEnabled(False)
                self.memory.setStyleSheet("color: #FF9AA7; font-weight: bold;")
                return
            size = estimate.file_size
        charge = allocated_size(size)
        caps = self.caps
        limit = caps.max_asset_size if caps else LIBRARY_MAX
        lines = [f"Кадры: {count} из {self.frame_count} · {width}×{height}",
                 f"Готовая сцена: {size / 1024:.1f} KiB ({size:,} Б). Учёт в библиотеке: {charge / 1024:.0f} KiB."]
        errors = []
        if optimized:
            lines.append(f"Без сжатия: {raw_size/1024:.1f} KiB → после оптимизации: {size/1024:.1f} KiB. Экономия {100*(1-size/raw_size):.1f}%.")
        if count < 2:
            errors.append("Выбери не менее двух кадров.")
        if size > limit:
            errors.append(f"Превышен предел файла на {(size-limit)/1024:.1f} KiB.")
        projected, budget = charge, LIBRARY_MAX
        if caps and caps.supports_library_budget:
            budget = caps.library_limit
            projected = caps.library_used - allocated_size(self.old_size) + charge
            available = max(0, budget - caps.library_used + allocated_size(self.old_size))
            lines.append(f"Доступно под эту сцену: {available/1024:.0f} KiB из {budget/1024:.0f} KiB библиотеки.")
            if projected > budget and not (caps.library_used > budget and projected < caps.library_used):
                errors.append(f"Не помещается в библиотеку: лишние {(projected-budget)/1024:.0f} KiB.")
        if caps:
            required = (charge if caps.supports_library_budget else size) + caps.service_reserve
            if required > caps.storage_free:
                errors.append(f"Для безопасной записи не хватает {(required-caps.storage_free)/1024:.1f} KiB свободной памяти.")
        else:
            lines.append("Заколка не подключена: доступное место неизвестно; показан предел формата.")
        if errors:
            lines.extend(errors)
            if estimate is not None:
                allowed=limit
                if caps and caps.supports_library_budget:
                    quota_available=max(0,caps.library_limit-caps.library_used+allocated_size(self.old_size))
                    if caps.library_used>caps.library_limit and self.old_size:
                        quota_available=max(quota_available,allocated_size(self.old_size)-caps.allocation_block)
                    allowed=min(allowed,quota_available//caps.allocation_block*caps.allocation_block)
                if caps:
                    physical=max(0,caps.storage_free-caps.service_reserve)
                    block=caps.allocation_block if caps.supports_library_budget else 1
                    allowed=min(allowed,physical//block*block)
                fitting=[n for n,bytes_needed in enumerate(estimate.prefix_sizes,1) if n>=2 and bytes_needed<=allowed]
                if fitting:
                    keep=max(fitting)
                    last_source=self.first.value()+(keep-1)*self.step.value()
                    lines.append(f'Поместятся первые {keep} из {count} выбранных кадров ({estimate.prefix_sizes[keep-1]/1024:.1f} KiB). '
                                 f'Сократи число кадров с {count} до {keep}: поставь «Последний кадр» = {last_source}.')
                elif count>=2:
                    lines.append('Даже первые 2 кадра не помещаются. Выбери меньший профиль или освободи место.')
            lines.append("Увеличь шаг, сократи диапазон или выбери меньший профиль.")
        else:
            lines.append(f"Помещается. После добавления останется {max(0,budget-projected)/1024:.0f} KiB." if caps and caps.supports_library_budget else "Размер файла допустим. Место на плате проверяется перед загрузкой.")
        self.memory.setText("\n".join(lines))
        self.memory.setStyleSheet("color: #FF9AA7; font-weight: bold;" if errors else "")
        self.memory_bar.setValue(min(1000, round(projected * 1000 / budget)))
        self.memory_bar.setFormat("Не помещается" if errors else f"{projected/1024:.0f} / {budget/1024:.0f} KiB")
        self.prepare_button.setEnabled(not errors)
        if errors:self.set_problem(ValueError('\n'.join(lines)))
        else:self.problem_button.hide()

    def set_problem(self,error):
        from ..problems import problem
        self.current_problem=problem(error,'image',context={'gif_path':str(self.gif_path),'options':self.options()})
        self.problem_button.show()

    @Slot()
    def show_problem(self):
        if self.current_problem is None:return
        from .notifications import ProblemDialog
        if self.problem_dialog is None:self.problem_dialog=ProblemDialog(self)
        self.problem_dialog.display(self.current_problem,self.current_problem.report())
        self.problem_dialog.show();self.problem_dialog.raise_()

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
            button.setMinimumHeight(40)
            button.setStyleSheet('padding: 4px;')
            button.clicked.connect(action)
            row.addWidget(button)
        return widget
