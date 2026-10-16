from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QPixmap
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QScrollArea,QWidget


class ComparisonDialog(QDialog):
    def __init__(self,before,after,parent=None):
        super().__init__(parent);self.setWindowTitle('До / после · точные пиксели RGB565')
        self.resize(900,540);layout=QVBoxLayout(self)
        layout.addWidget(QLabel('Слева — прежний Bicubic без фильтров. Справа — результат обработки. Положение одинаковое.'))
        zoom=QComboBox()
        for scale in (1,2,4,8):zoom.addItem(f'{scale*100} %',scale)
        layout.addWidget(zoom)
        scroll=QScrollArea();scroll.setWidgetResizable(True);content=QWidget();row=QHBoxLayout(content)
        labels=[];pixmaps=[]
        for title,image in (('До',before),('После · отправляем на заколку',after)):
            column=QVBoxLayout();column.addWidget(QLabel(title));label=QLabel()
            label.setAlignment(Qt.AlignTop|Qt.AlignLeft);column.addWidget(label);row.addLayout(column)
            labels.append(label)
            pixmaps.append(QPixmap.fromImage(QImage(image.tobytes(),image.width,image.height,image.width*3,QImage.Format_RGB888).copy()))
        scroll.setWidget(content);layout.addWidget(scroll)
        def update():
            scale=zoom.currentData()
            for label,pixmap in zip(labels,pixmaps):
                label.setPixmap(pixmap.scaled(pixmap.width()*scale,pixmap.height()*scale,Qt.IgnoreAspectRatio,Qt.FastTransformation))
        zoom.currentIndexChanged.connect(update);update()
