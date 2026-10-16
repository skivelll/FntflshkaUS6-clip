from PySide6.QtCore import Qt,Slot
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QComboBox, QCheckBox, QPushButton, QDialogButtonBox
from ..library.restore import make_plan


class RestoreDialog(QDialog):
    def __init__(self, collection, snapshot, parent=None):
        super().__init__(parent)
        self.collection, self.snapshot, self.plan = collection, snapshot, None
        self.setWindowTitle("План восстановления")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        intro = QLabel("Выбери действие для каждой сцены. Остальные сцены сохранятся. Перед записью создаётся локальная резервная копия платы.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = QTableWidget(len(collection.scenes), 2)
        self.table.setHorizontalHeaderLabels(["Из коллекции", "На заколку"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.choices = []
        for row, scene in enumerate(collection.scenes):
            item = QTableWidgetItem(f"ID {scene.id} · {scene.title or 'Без имени'}")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, item)
            choice = QComboBox()
            choice.addItem("Добавить в свободное место", "add")
            choice.addItem("Пропустить", None)
            for existing in snapshot.scenes:
                choice.addItem(f"Заменить ID {existing.id} · {existing.label}", existing.id)
            choice.currentIndexChanged.connect(self.recalculate)
            self.choices.append(choice)
            self.table.setCellWidget(row, 1, choice)
        self.table.setColumnWidth(0, 250)
        layout.addWidget(self.table)
        same_ids = QPushButton("Сопоставить по ID (замена совпадающих)")
        same_ids.clicked.connect(self.match_ids)
        layout.addWidget(same_ids)
        self.metadata = QCheckBox("Применить названия и относительный порядок коллекции")
        self.metadata.setChecked(snapshot.capabilities.supports_scene_metadata)
        self.metadata.setEnabled(snapshot.capabilities.supports_scene_metadata)
        self.active = QCheckBox("Применить активную сцену из коллекции")
        self.active.setChecked(True)
        self.metadata.toggled.connect(self.recalculate)
        self.active.toggled.connect(self.recalculate)
        layout.addWidget(self.metadata)
        layout.addWidget(self.active)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.problem_button=QPushButton('? Почему восстановление недоступно?')
        self.problem_button.hide();self.problem_button.clicked.connect(self.show_problem)
        layout.addWidget(self.problem_button)
        self.current_problem=None;self.problem_dialog=None
        warning = QLabel("Каждая сцена сохраняется отдельно. При обрыве уже завершённые шаги останутся, остальные не повторяются автоматически. Текущий незавершённый шаг нужно проверить по списку сцен.")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Восстановить по плану")
        self.buttons.button(QDialogButtonBox.Ok).setAutoDefault(False)
        self.buttons.button(QDialogButtonBox.Cancel).setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.recalculate()

    def match_ids(self):
        for source, choice in zip(self.collection.scenes, self.choices):
            index = choice.findData(source.id)
            choice.setCurrentIndex(index if index >= 0 else 0)

    def recalculate(self, *args):
        if not hasattr(self, "buttons"):
            return
        try:
            self.plan = make_plan(self.collection, self.snapshot, [c.currentData() for c in self.choices], self.metadata.isChecked(), self.active.isChecked())
            replacements = [item.target_id for item in self.plan.items if item.previous_checksum is not None]
            additions = [item.target_id for item in self.plan.items if item.previous_checksum is None]
            self.summary.setText(f"Добавить: {additions}; заменить: {replacements}\n"
                f"Оценка с запасом: ≈{self.plan.estimated_bytes:,} Б / свободно {self.snapshot.capabilities.storage_free:,} Б\n"
                f"Порядок после применения metadata: {list(self.plan.final_order) if self.plan.metadata else 'сохранится текущий'}; ACTIVE {self.plan.active_id}.")
            self.problem_button.hide()
        except ValueError as error:
            self.plan = None
            self.summary.setText(str(error))
            from ..problems import problem
            self.current_problem=problem(error,'validation',context={'collection':self.collection.title,'port':self.snapshot.port})
            self.problem_button.show()
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(self.plan is not None)

    @Slot()
    def show_problem(self):
        if self.current_problem is None:return
        from .notifications import ProblemDialog
        if self.problem_dialog is None:self.problem_dialog=ProblemDialog(self)
        self.problem_dialog.display(self.current_problem,self.current_problem.report())
        self.problem_dialog.show();self.problem_dialog.raise_()
