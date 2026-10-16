from PySide6.QtCore import Qt, QRectF, Signal, QTimer, QPointF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget
from time import monotonic
import math


class Preview(QWidget):
    """Keep native pixel geometry independent of window size."""
    drag_started = Signal()
    dragged = Signal(float, float)
    zoom_requested = Signal(float, object)
    paint_started=Signal(int,int);paint_moved=Signal(int,int);paint_finished=Signal()
    paint_zoom_changed=Signal(int)

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
        self.drawing=False;self.grid=False;self.view_zoom=1;self.view_pan=QPointF();self._paint_down=False;self._pan_origin=None
        self.setToolTip("В режиме редактирования: тяни картинку мышью; колесо меняет масштаб вокруг курсора.")

    def set_interactive(self, enabled):
        self.interactive = bool(enabled)
        if not enabled:
            self._drag_origin = None
        self.setCursor(Qt.OpenHandCursor if enabled else Qt.ArrowCursor)

    def display_rect(self):
        width, height = self.target
        scale = max(0.01, min((self.width() - 24) / width, (self.height() - 24) / height))
        scale*=self.view_zoom
        return QRectF((self.width() - width * scale) / 2+self.view_pan.x(), (self.height() - height * scale) / 2+self.view_pan.y(),
                      width * scale, height * scale)

    def mousePressEvent(self, event):
        if self.drawing and self.interactive and not self.image.isNull():
            if event.button()==Qt.MiddleButton:
                self._pan_origin=event.position();event.accept();return
            if event.button()==Qt.LeftButton and self.display_rect().contains(event.position()):
                x,y=self.pixel_position(event.position())
                if 0<=x<self.target[0] and 0<=y<self.target[1]:
                    self._paint_down=True;self.paint_started.emit(x,y)
                event.accept();return
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
        if self._pan_origin is not None:
            self.view_pan+=event.position()-self._pan_origin;self._pan_origin=event.position();self.update();event.accept();return
        if self._paint_down:
            self.paint_moved.emit(*self.pixel_position(event.position()));event.accept();return
        if self._drag_origin is not None:
            self.drag_to(event.position())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pan_origin is not None and event.button()==Qt.MiddleButton:self._pan_origin=None;event.accept();return
        if self._paint_down and event.button()==Qt.LeftButton:
            self.paint_moved.emit(*self.pixel_position(event.position()));self._paint_down=False;self.paint_finished.emit();event.accept();return
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
            if self.drawing:
                old=self.view_zoom;self.view_zoom=max(1,min(8,old*(2 if steps>0 else .5)))
                center=QPointF(self.width()/2,self.height()/2)
                self.view_pan=event.position()-center-(event.position()-center-self.view_pan)*(self.view_zoom/old)
                self.paint_zoom_changed.emit(int(self.view_zoom));self.update();event.accept();return
            anchor = ((event.position().x() - rect.center().x()) * self.target[0] / rect.width(),
                      (event.position().y() - rect.center().y()) * self.target[1] / rect.height())
            self.zoom_requested.emit(steps, anchor)
        event.accept()

    def pixel_position(self,position):
        rect=self.display_rect()
        return (math.floor((position.x()-rect.x())*self.target[0]/rect.width()),math.floor((position.y()-rect.y())*self.target[1]/rect.height()))

    def set_drawing(self,enabled,grid=False):
        if self.drawing!=enabled:
            self._paint_down=False;self._pan_origin=None;self.view_zoom=1;self.view_pan=QPointF()
        self.drawing=enabled;self.grid=grid
        self.setCursor(Qt.CrossCursor if enabled else Qt.OpenHandCursor if self.interactive else Qt.ArrowCursor);self.update()

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
        if self.drawing and self.grid and rect.width()/self.target[0]>=8:
            painter.setClipRect(rect);painter.setPen(QColor(169,155,234,100))
            for x in range(self.target[0]+1):
                px=rect.x()+x*rect.width()/self.target[0];painter.drawLine(QPointF(px,rect.top()),QPointF(px,rect.bottom()))
            for y in range(self.target[1]+1):
                py=rect.y()+y*rect.height()/self.target[1];painter.drawLine(QPointF(rect.left(),py),QPointF(rect.right(),py))
