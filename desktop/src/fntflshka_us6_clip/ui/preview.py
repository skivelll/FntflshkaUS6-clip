from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget


class Preview(QWidget):
    """Keep native pixel geometry independent of window size."""
    drag_started = Signal()
    dragged = Signal(float, float)
    zoom_requested = Signal(float, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.target = (320, 172)
        self.image = QImage()
        self.setMinimumSize(320, 220)
        self.interactive = False
        self._drag_origin = None
        self.setToolTip("В режиме редактирования: тяни картинку мышью; колесо меняет масштаб вокруг курсора.")

    def set_interactive(self, enabled):
        self.interactive = bool(enabled)
        if not enabled:
            self._drag_origin = None
        self.setCursor(Qt.OpenHandCursor if enabled else Qt.ArrowCursor)

    def display_rect(self):
        width, height = self.target
        scale = max(0.01, min((self.width() - 24) / width, (self.height() - 24) / height))
        return QRectF((self.width() - width * scale) / 2, (self.height() - height * scale) / 2,
                      width * scale, height * scale)

    def mousePressEvent(self, event):
        if self.interactive and not self.image.isNull() and event.button() == Qt.LeftButton and self.display_rect().contains(event.position()):
            self._drag_origin = event.position()
            self.setCursor(Qt.ClosedHandCursor)
            self.drag_started.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def drag_to(self, position):
        delta = position - self._drag_origin
        rect = self.display_rect()
        self.dragged.emit(delta.x() * self.target[0] / rect.width(),
                          delta.y() * self.target[1] / rect.height())

    def mouseMoveEvent(self, event):
        if self._drag_origin is not None:
            self.drag_to(event.position())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_origin is not None and event.button() == Qt.LeftButton:
            self.drag_to(event.position())
            self._drag_origin = None
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        rect = self.display_rect()
        if not self.interactive or self.image.isNull() or not rect.contains(event.position()):
            event.ignore()
            return
        steps = event.angleDelta().y() / 120 if event.angleDelta().y() else event.pixelDelta().y() / 40
        if steps:
            anchor = ((event.position().x() - rect.center().x()) * self.target[0] / rect.width(),
                      (event.position().y() - rect.center().y()) * self.target[1] / rect.height())
            self.zoom_requested.emit(steps, anchor)
        event.accept()

    def set_image(self, image):
        self.target = image.size
        self.image = QImage(image.tobytes(), image.width, image.height, image.width * 3,
                            QImage.Format_RGB888).copy()
        self.update()

    def clear_image(self):
        self.image = QImage()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#17121F"))
        rect = self.display_rect()
        painter.fillRect(rect, Qt.black)
        if not self.image.isNull():
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.drawImage(rect, self.image)
        else:
            painter.setPen(QColor("#9C91A8"))
            painter.drawText(rect, Qt.AlignCenter, "Добавь картинку ✦")
        painter.setPen(QColor("#A99BEA"))
        painter.drawRect(rect)
