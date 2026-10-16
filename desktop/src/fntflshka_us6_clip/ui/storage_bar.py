from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget


class StorageBar(QWidget):
    """Current bytes plus one explicitly approximate operation overlay."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.projection = None
        self.setFixedHeight(18)
        self.setAccessibleName("Память LittleFS")

    def set_projection(self, projection):
        self.projection = projection
        if projection:
            p = projection
            if p.library:
                text = (f"Библиотека: {p.used:,} / {p.total:,} Б (файлы округлены до блоков 4096 Б). "
                        f"Свободно под контент {p.free:,} Б. После операции ≈{p.projected_free:,} Б. "
                        f"Физически свободно {p.physical_free:,} Б; служебный запас {p.reserve:,} Б. "
                        "При замене старая копия сохраняется до commit; резерв не является местом под контент.")
                self.setToolTip(text)
                self.setAccessibleDescription(text)
                self.update()
                return
            text = f"Занято {p.used:,} Б; свободно {p.free:,} Б из {p.total:,} Б."
            if p.upload:
                text += f" Розовый: новый файл {p.upload:,} Б, без вычитания старого A/B. Служебный запас — ещё 8192 Б."
            if p.reclaim:
                text += f" Бирюзовый: удаляемый файл {p.reclaim:,} Б. Прогноз приблизительный: старый A/B и блоки LittleFS не учтены."
            self.setToolTip(text)
            self.setAccessibleDescription(text)
        else:
            self.setToolTip("Подключи заколку, чтобы узнать свободное место.")
            self.setAccessibleDescription(self.toolTip())
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.fillRect(rect, QColor("#30243D"))
        p = self.projection
        if p and p.total:
            def fill(start, count, color):
                painter.fillRect(QRectF(rect.left() + rect.width() * start / p.total, rect.top(),
                                        rect.width() * count / p.total, rect.height()), QColor(color))
            fill(0, min(p.used, p.total), "#756C7E")
            if p.reclaim:
                start = max(0, min(p.total, p.used - p.reclaim))
                fill(start, min(p.reclaim, p.total - start), "#65C7BA")
            if p.upload:
                start = min(p.total, max(0, p.used - (p.reclaim if p.library else 0)))
                fits = p.used - p.reclaim + p.upload <= p.total if p.library else p.upload + p.reserve <= p.free
                fill(start, min(p.upload, p.total - start), "#E98CBC" if fits else "#E46B77")
        painter.setPen(QColor("#66527B"))
        painter.drawRect(rect)
