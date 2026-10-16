from PySide6.QtCore import Qt, QRectF, Signal, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget
from time import monotonic
import math


class Preview(QWidget):
    """Keep native pixel geometry independent of window size."""
    drag_started = Signal()
    dragged = Signal(float, float)
    zoom_requested = Signal(float, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.target = (320, 172)
        self.image = QImage()
        self.animation = ()
        self.animation_index = 0
        self.animation_timer = QTimer(self)
        self.animation_timer.setSingleShot(True)
        self.animation_timer.setTimerType(Qt.PreciseTimer)
        self.animation_timer.timeout.connect(self.advance_frame)
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
        self.animation_timer.stop()
        self.animation = ()
        self.target = image.size
        self.image = QImage(image.tobytes(), image.width, image.height, image.width * 3,
                            QImage.Format_RGB888).copy()
        self.update()

    def set_animation(self, frames, start_index=0):
        if not frames:
            self.clear_image()
            return
        start_index=max(0,min(len(frames)-1,start_index))
        self.set_image(frames[start_index][0])
        self.animation = frames
        self.animation_index = start_index
        self.animation_durations=tuple(ms for _,ms in getattr(frames,'frames',frames))
        self.animation_cycle=sum(self.animation_durations)/1000
        self.animation_deadline=monotonic()+self.animation_durations[start_index]/1000
        if self.isVisible():
            self.animation_timer.start(frames[start_index][1])

    def advance_frame(self):
        if not self.animation:
            return
        frames = self.animation
        index = (self.animation_index + 1) % len(frames)
        now=monotonic()
        deadline=self.animation_deadline+self.animation_durations[index]/1000
        if now>=deadline:
            deadline+=int((now-deadline)/self.animation_cycle)*self.animation_cycle
            while deadline<=now:
                index=(index+1)%len(frames)
                deadline+=self.animation_durations[index]/1000
        self.set_image(frames[index][0])
        self.animation, self.animation_index = frames, index
        self.animation_deadline=deadline
        self.animation_timer.start(max(1,math.ceil((deadline-monotonic())*1000)))

    def hideEvent(self, event):
        self.animation_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self.animation:
            self.animation_deadline=monotonic()+self.animation_durations[self.animation_index]/1000
            self.animation_timer.start(self.animation[self.animation_index][1])

    def clear_image(self):
        self.animation_timer.stop()
        self.animation = ()
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
