from dataclasses import replace
from PySide6.QtCore import Signal,Qt,QSignalBlocker
from PySide6.QtGui import QFont,QColor
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QListWidget,QPushButton,
    QPlainTextEdit,QFontComboBox,QSpinBox,QComboBox,QCheckBox,QColorDialog,QAbstractSpinBox,QToolButton)
from ..media.text_model import TextLayer


class TextControls(QWidget):
    changed=Signal()

    def __init__(self):
        super().__init__();self.layers=();self.history=[];self.future=[];self.loading=False
        layout=QVBoxLayout(self);layout.setContentsMargins(8,8,8,8);layout.setSpacing(4)
        self.blank=QPushButton('Новая сцена · только текст')
        self.blank.hide()
        self.list=QListWidget();self.list.setMaximumHeight(80);layout.addWidget(self.list)
        row=QHBoxLayout()
        for label,action in (('+ Слой',self.add),('Удалить',self.remove),('↑',lambda:self.move(-1)),('↓',lambda:self.move(1))):
            button=QPushButton(label);button.clicked.connect(action);row.addWidget(button)
            if label in ('↑','↓'):button.setFixedWidth(44)
        layout.addLayout(row)
        self.text=QPlainTextEdit();self.text.setMaximumHeight(64);self.text.setUndoRedoEnabled(False)
        self.text.setPlaceholderText('Русский и латинский текст · до 512 символов');layout.addWidget(self.text)
        self.text_count=QLabel('До 8 слоёв, до 512 символов в каждом');layout.addWidget(self.text_count)
        self.font=QFontComboBox();layout.addWidget(self.font)
        self.size=QSpinBox();self.size.setRange(8,96);self.size.setSuffix(' px')
        self.color=QPushButton('Цвет текста');self.color.clicked.connect(lambda:self.choose_color('color'))
        row=QHBoxLayout();row.addWidget(QLabel('Размер'));row.addWidget(self.number(self.size));row.addWidget(self.color);layout.addLayout(row)
        self.x=QSpinBox();self.y=QSpinBox();self.width=QSpinBox();self.width.setRange(16,320)
        row=QHBoxLayout()
        for name,spin in (('X',self.x),('Y',self.y)):
            if spin is not self.width:spin.setRange(-640,640)
            row.addWidget(QLabel(name));row.addWidget(self.number(spin))
        layout.addLayout(row)
        self.align=QComboBox()
        for name,key in (('Слева','left'),('По центру','center'),('Справа','right')):self.align.addItem(name,key)
        row=QHBoxLayout();row.addWidget(QLabel('Перенос'));row.addWidget(self.number(self.width));row.addWidget(self.align);layout.addLayout(row)
        self.outline=QSpinBox();self.outline.setRange(0,3)
        self.outline_color=QPushButton('Цвет обводки');self.outline_color.clicked.connect(lambda:self.choose_color('outline_color'))
        row=QHBoxLayout();row.addWidget(QLabel('Обводка'));row.addWidget(self.number(self.outline));row.addWidget(self.outline_color);layout.addLayout(row)
        self.background=QCheckBox('Подложка текста')
        self.background_color=QPushButton('Цвет подложки');self.background_color.clicked.connect(lambda:self.choose_color('background_color'))
        row=QHBoxLayout();row.addWidget(self.background);row.addWidget(self.background_color);layout.addLayout(row)
        self.motion=QComboBox()
        for name,key in (('Статичный текст','static'),('Бегущий · справа налево','left'),('Бегущий · слева направо','right')):self.motion.addItem(name,key)
        layout.addWidget(self.motion)
        self.speed=QSpinBox();self.speed.setRange(10,120);self.speed.setValue(40);self.speed.setSuffix(' px/s')
        self.pause=QSpinBox();self.pause.setRange(0,2000);self.pause.setSingleStep(100);self.pause.setSuffix(' ms')
        self.motion_options=QWidget();row=QHBoxLayout(self.motion_options);row.setContentsMargins(0,0,0,0)
        row.addWidget(QLabel('Скорость'));row.addWidget(self.number(self.speed));row.addWidget(QLabel('Пауза'));row.addWidget(self.number(self.pause))
        layout.addWidget(self.motion_options)
        row=QHBoxLayout();self.undo_button=QPushButton('↶ Отменить');self.redo_button=QPushButton('↷ Повторить')
        self.undo_button.clicked.connect(self.undo);self.redo_button.clicked.connect(self.redo)
        row.addWidget(self.undo_button);row.addWidget(self.redo_button);layout.addLayout(row)
        help=QLabel('Drag — выбранный слой; колесо — размер. ↑/↓ — порядок. Движется вся группа, до ~3 кадров/с. Размер — после подготовки.')
        help.setWordWrap(True);layout.addWidget(help);layout.addStretch()
        self.list.currentRowChanged.connect(self.load_selected)
        for signal in (self.text.textChanged,self.font.currentFontChanged,self.size.valueChanged,self.x.valueChanged,
                       self.y.valueChanged,self.width.valueChanged,self.align.currentIndexChanged,self.outline.valueChanged,self.background.toggled):
            signal.connect(self.edit)
        for signal in (self.motion.currentIndexChanged,self.speed.valueChanged,self.pause.valueChanged):signal.connect(self.settings_changed)
        self.set_document()

    def number(self,spin):
        host=QWidget();row=QHBoxLayout(host);row.setContentsMargins(0,0,0,0);row.setSpacing(2)
        spin.setButtonSymbols(QAbstractSpinBox.NoButtons);spin.setKeyboardTracking(False)
        spin.setFixedWidth(68);row.addWidget(spin)
        for label,action in (('▼',spin.stepDown),('▲',spin.stepUp)):
            button=QToolButton();button.setText(label);button.setFixedSize(24,32)
            button.setStyleSheet('padding: 0; border: 1px solid #66527B; border-radius: 4px;')
            button.setAutoRepeat(True);button.clicked.connect(action);row.addWidget(button)
        row.addStretch();return host

    def state(self):return (self.layers,self.motion.currentData(),self.speed.value(),self.pause.value())
    def checkpoint(self):
        state=self.state()
        if not self.history or self.history[-1]!=state:self.history.append(state);self.history=self.history[-101:];self.future=[]
        self.undo_button.setEnabled(len(self.history)>1);self.redo_button.setEnabled(bool(self.future))
        self.changed.emit()

    def set_document(self,layers=(),motion='static',speed=40,pause=0):
        self.loading=True;self.layers=tuple(layers)
        self.motion.setCurrentIndex(self.motion.findData(motion));self.speed.setValue(speed);self.pause.setValue(pause)
        self.refresh(0 if layers else -1);self.loading=False
        self.history=[self.state()];self.future=[];self.undo_button.setEnabled(False);self.redo_button.setEnabled(False)
        self.motion_options.setVisible(motion!='static')

    def refresh(self,index):
        blocker=QSignalBlocker(self.list);self.list.clear()
        for i,layer in enumerate(self.layers):self.list.addItem(f'{i+1}. '+(layer.text.replace('\n',' ')[:40] or 'Пустой слой'))
        self.list.setCurrentRow(index);del blocker;self.load_selected(index)

    def selected(self):
        index=self.list.currentRow()
        return self.layers[index] if 0<=index<len(self.layers) else None

    def load_selected(self,*_):
        previous=self.loading;self.loading=True;layer=self.selected()
        for widget in (self.text,self.font,self.size,self.x,self.y,self.width,self.align,self.outline,self.background,self.color,self.outline_color,self.background_color):widget.setEnabled(layer is not None)
        if layer:
            self.text.setPlainText(layer.text);self.font.setCurrentFont(QFont(layer.font))
            self.text_count.setText(f'{len(layer.text)} / 512 символов')
            for key in ('size','x','y','width','outline'):getattr(self,key).setValue(getattr(layer,key))
            self.align.setCurrentIndex(self.align.findData(layer.align));self.background.setChecked(layer.background)
            for key in ('color','outline_color','background_color'):getattr(self,key).setStyleSheet(f'border: 2px solid {getattr(layer,key)};')
        self.loading=previous

    def add(self):
        if len(self.layers)>=8:
            self.text_count.setText('Уже 8 слоёв. Удали один, чтобы добавить новый.');return
        self.layers+= (TextLayer(font=self.font.currentFont().family()),)
        self.refresh(len(self.layers)-1);self.checkpoint()

    def remove(self):
        index=self.list.currentRow()
        if self.selected() is None:return
        self.layers=self.layers[:index]+self.layers[index+1:];self.refresh(min(index,len(self.layers)-1));self.checkpoint()

    def move(self,delta):
        index=self.list.currentRow();target=index+delta
        if not 0<=index<len(self.layers) or not 0<=target<len(self.layers):return
        layers=list(self.layers);layers[index],layers[target]=layers[target],layers[index];self.layers=tuple(layers)
        self.refresh(target);self.checkpoint()

    def edit(self,*_):
        if self.loading or self.selected() is None:return
        index=self.list.currentRow();text=self.text.toPlainText()
        self.text_count.setText(f'{len(text)} / 512 символов'+(' · сократи текст для загрузки' if len(text)>512 else ''))
        layer=replace(self.layers[index],text=text,font=self.font.currentFont().family(),size=self.size.value(),x=self.x.value(),y=self.y.value(),width=self.width.value(),align=self.align.currentData(),outline=self.outline.value(),background=self.background.isChecked())
        self.layers=self.layers[:index]+(layer,)+self.layers[index+1:]
        self.list.item(index).setText(f'{index+1}. '+(text.replace('\n',' ')[:40] or 'Пустой слой'));self.checkpoint()

    def settings_changed(self,*_):
        self.motion_options.setVisible(self.motion.currentData()!='static')
        if not self.loading:self.checkpoint()

    def choose_color(self,key):
        layer=self.selected()
        if layer is None:return
        color=QColorDialog.getColor(QColor(getattr(layer,key)),self,'Цвет текста')
        if color.isValid():
            index=self.list.currentRow();self.layers=self.layers[:index]+(replace(layer,**{key:color.name()}),)+self.layers[index+1:]
            self.load_selected();self.checkpoint()

    def set_offset(self,x,y):
        if self.selected() is None:return
        self.loading=True;self.x.setValue(round(x));self.y.setValue(round(y));self.loading=False;self.edit()

    def undo(self):
        if len(self.history)<2:return
        self.future.append(self.history.pop());self.apply_state(self.history[-1])

    def redo(self):
        if not self.future:return
        state=self.future.pop();self.history.append(state);self.apply_state(state)

    def apply_state(self,state):
        history,future=self.history,self.future
        self.set_document(*state);self.history,self.future=history,future
        self.undo_button.setEnabled(len(history)>1);self.redo_button.setEnabled(bool(future));self.changed.emit()
