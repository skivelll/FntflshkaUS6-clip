from PySide6.QtCore import Signal, QSize, Qt, QPointF
from PySide6.QtGui import QColor, QIcon, QPixmap, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QSpinBox,QCheckBox,QColorDialog,QAbstractSpinBox
from ..media.drawing import DrawingDocument


def tool_icon(tool):
    pixmap=QPixmap(28,28);pixmap.fill(Qt.transparent)
    painter=QPainter(pixmap);painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor('#eee6ff'),1.8,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
    painter.setBrush(QColor('#7566d9'))
    def polygon(points):painter.drawPolygon(QPolygonF([QPointF(x,y) for x,y in points]))
    if tool=='pencil':
        polygon([(5,23),(7,16),(20,3),(25,8),(12,21)])
        painter.drawLine(8,16,12,20);painter.drawLine(17,6,22,11)
    elif tool=='eraser':
        polygon([(4,17),(15,4),(25,13),(16,23),(11,23)])
        painter.drawLine(10,11,21,18);painter.drawLine(5,24,24,24)
    elif tool=='fill':
        polygon([(3,13),(13,3),(23,13),(13,23)])
        painter.drawLine(7,9,18,9)
        painter.setBrush(QColor('#58c7ff'));polygon([(24,16),(21,22),(24,25),(27,22)])
    else:
        polygon([(4,24),(6,17),(17,6),(22,11),(11,22)])
        painter.drawLine(14,5,23,14);painter.drawLine(18,4,24,10)
    painter.end();return QIcon(pixmap)


class DrawingControls(QWidget):
    changed=Signal();view_changed=Signal();grid_changed=Signal();fill_requested=Signal(object);clear_requested=Signal()

    def __init__(self):
        super().__init__();self.document=DrawingDocument();self.color='#ffffff';self.fill_busy=False
        layout=QVBoxLayout(self);layout.addWidget(QLabel('РИСОВАНИЕ · отдельный слой'))
        self.tool=QComboBox()
        for name,key in (('Карандаш','pencil'),('Ластик','eraser'),('Заливка слоя','fill'),('Пипетка с preview','pick')):self.tool.addItem(name,key)
        layout.addWidget(self.tool)
        self.tool.setIconSize(QSize(28,28))
        for index in range(self.tool.count()):self.tool.setItemIcon(index,tool_icon(self.tool.itemData(index)))
        row=QHBoxLayout();row.addWidget(QLabel('Кисть, px'))
        self.size=QSpinBox();self.size.setRange(1,16);self.size.setButtonSymbols(QAbstractSpinBox.NoButtons);row.addWidget(self.size)
        for label,action in (('▼',self.size.stepDown),('▲',self.size.stepUp)):
            button=QPushButton(label);button.setFixedWidth(44);button.setAutoRepeat(True);button.clicked.connect(action);row.addWidget(button)
        layout.addLayout(row)
        self.color_button=QPushButton('Цвет · #ffffff');self.color_button.clicked.connect(self.choose_color);layout.addWidget(self.color_button)
        for colors in (('#ffffff','#000000','#ff506a','#ffb547','#ffe66a'),('#69e0af','#58c7ff','#7566d9','#e98cbc','#b5ae9c')):
            row=QHBoxLayout()
            for color in colors:
                button=QPushButton('■');button.setStyleSheet(f'color:{color};padding:6px;');button.clicked.connect(lambda _,c=color:self.set_color(c));row.addWidget(button)
            layout.addLayout(row)
        self.grid=QCheckBox('Пиксельная сетка при увеличении');self.grid.setChecked(True);self.grid.toggled.connect(self.grid_changed);layout.addWidget(self.grid)
        self.zoom=QComboBox()
        for zoom in (1,2,4,8):self.zoom.addItem(f'Увеличение preview · {zoom}×',zoom)
        self.zoom.currentIndexChanged.connect(self.view_changed);layout.addWidget(self.zoom)
        reset=QPushButton('Вернуть вид по центру');reset.clicked.connect(self.view_changed);layout.addWidget(reset)
        row=QHBoxLayout();self.undo_button=QPushButton('↶ Отменить штрих');self.redo_button=QPushButton('↷ Повторить')
        self.undo_button.clicked.connect(self.undo);self.redo_button.clicked.connect(self.redo);row.addWidget(self.undo_button);row.addWidget(self.redo_button);layout.addLayout(row)
        clear=QPushButton('Очистить рисунок…');clear.clicked.connect(self.clear_requested);layout.addWidget(clear)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        help=QLabel('ЛКМ — рисовать. Колесо — zoom preview, средняя кнопка — панорамирование. Сетка видна на крупных пикселях.\nЛастик убирает только рисунок. Заливка ищет границы в этом слое, не в фото. Сохрани проект для повторного редактирования.')
        help.setWordWrap(True);layout.addWidget(help);layout.addStretch();self.update_history()

    def set_document(self,pixels=b'',target=(320,172)):
        self.document=DrawingDocument(target,pixels);self.fill_busy=False;self.update_history()

    def set_color(self,color):self.color=color;self.color_button.setText('Цвет · '+color)

    def choose_color(self):
        color=QColorDialog.getColor(QColor(self.color),self,'Цвет кисти')
        if color.isValid():self.set_color(color.name())

    def start(self,x,y):
        if self.fill_busy:return
        tool=self.tool.currentData()
        if tool=='fill':
            self.fill_busy=True;self.status.setText('Заливаем область…')
            self.fill_requested.emit({'pixels':self.document.image.tobytes(),'target':self.document.target,'point':(x,y),'color':self.color,'revision':self.document.revision})
        elif tool in ('pencil','eraser'):
            self.document.begin((x,y),self.color,self.size.value(),tool=='eraser');self.changed.emit()

    def move(self,x,y):
        if self.fill_busy or self.document.last is None:return
        self.document.line((x,y));self.changed.emit()

    def finish(self):
        if self.document.last is not None:self.document.end();self.update_history();self.changed.emit()

    def filled(self,revision,pixels):
        self.fill_busy=False
        if revision!=self.document.revision:return
        self.document.replace(pixels);self.update_history();self.changed.emit()

    def update_history(self):
        self.undo_button.setEnabled(len(self.document.history)>1);self.redo_button.setEnabled(bool(self.document.future))
        self.status.setText(f'{self.document.target[0]}×{self.document.target[1]} · {len(self.document.history)-1} шагов undo / 50')

    def undo(self):
        if not self.fill_busy:self.document.undo();self.update_history();self.changed.emit()

    def redo(self):
        if not self.fill_busy:self.document.redo();self.update_history();self.changed.emit()

    def clear(self):self.document.clear();self.update_history();self.changed.emit()
