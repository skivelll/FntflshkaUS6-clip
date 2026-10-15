from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget


class Preview(QWidget):
    """Keep native pixel geometry independent of window size."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.target = (320, 172)
        self.image = QImage()
        self.setMinimumSize(320, 220)

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
        width, height = self.target
        scale = min((self.width() - 24) / width, (self.height() - 24) / height)
        rect = QRectF((self.width() - width * scale) / 2, (self.height() - height * scale) / 2,
                      width * scale, height * scale)
        painter.fillRect(rect, Qt.black)
        if not self.image.isNull():
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.drawImage(rect, self.image)
        else:
            painter.setPen(QColor("#9C91A8"))
            painter.drawText(rect, Qt.AlignCenter, "Добавь картинку ✦")
        painter.setPen(QColor("#A99BEA"))
        painter.drawRect(rect)
