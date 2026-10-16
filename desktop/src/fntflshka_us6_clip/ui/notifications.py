"""Explicit non-blocking error windows; no network, serial access or write retry."""
from PySide6.QtCore import QObject,Qt,Slot,Signal,QTimer
from PySide6.QtWidgets import (QApplication,QDialog,QVBoxLayout,QHBoxLayout,QLabel,
                              QPushButton,QPlainTextEdit,QToolButton)
from shiboken6 import isValid
import logging
from dataclasses import replace


class ProblemDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.resize(640,440)
        layout=QVBoxLayout(self)
        self.heading=QLabel();self.heading.setStyleSheet('font-size: 20px; font-weight: bold; color: #FF9AA7;')
        self.message=QLabel();self.action=QLabel();self.state=QLabel()
        for label in (self.heading,self.message,self.action,self.state):
            label.setTextFormat(Qt.PlainText);label.setWordWrap(True);layout.addWidget(label)
        self.toggle=QToolButton();self.toggle.setText('? Подробнее');self.toggle.setCheckable(True)
        layout.addWidget(self.toggle)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.hide()
        self.details.setStyleSheet('font-family: Consolas, monospace; font-size: 12px;')
        self.details.setMinimumHeight(180);layout.addWidget(self.details)
        self.toggle.toggled.connect(self.details.setVisible)
        self.queued=QLabel();self.queued.setWordWrap(True);layout.addWidget(self.queued)
        row=QHBoxLayout();self.copy_button=QPushButton('Скопировать отчёт');self.close_button=QPushButton('Понятно')
        row.addWidget(self.copy_button);row.addStretch();row.addWidget(self.close_button);layout.addLayout(row)
        self.copy_button.clicked.connect(self.copy_report);self.close_button.clicked.connect(self.accept)
        self.close_button.setDefault(True)

    def display(self,value,report):
        self.setWindowTitle(value.title+' — FntflshkaUS6 clip')
        self.heading.setText(value.title);self.message.setText(value.message)
        self.action.setText('Что сделать: '+value.action);self.state.setText(value.state)
        self.details.setPlainText(report);self.copy_button.setText('Скопировать отчёт')
        self.toggle.setChecked(False)

    @Slot()
    def copy_report(self):
        self.toggle.setChecked(True)
        QApplication.clipboard().setText(self.details.toPlainText())
        self.copy_button.setText('Отчёт скопирован ✓')


class Notifications(QObject):
    changed=Signal(object)
    resolved=Signal(str)

    def __init__(self,owner,context=lambda: {}):
        super().__init__(owner)
        self.owner=owner;self.context=context;self.last=None;self.dialog=None
        self.seen=set();self.sound=False;self.closing=False
        self.pending=[];self.overflow=0
        self.unread=False

    @Slot(object)
    def present(self,value):
        if self.closing:return
        if self.dialog is not None and not isValid(self.dialog):self.dialog=None
        key=('connection',) if value.operation=='connection' else value.key
        if key in self.seen:return
        logging.getLogger(__name__).warning('Problem operation=%s cause=%s: %s state=%s',value.operation,value.exception_type,value.exception_message,value.state)
        if len(self.seen)>64:self.seen.clear()
        self.seen.add(key)
        if value.operation=='connection' and self.last and self.last.operation!='connection' and self.dialog and self.dialog.isVisible():
            return
        if self.dialog and self.dialog.isVisible() and self.last and self.last.operation!='connection':
            if len(self.pending)<16:self.pending.append(value)
            else:self.overflow+=1
            self.update_queue_label()
            if self.sound:QApplication.beep()
            return
        self.last=value;self.changed.emit(value)
        self.show_last()
        if self.sound:QApplication.beep()

    @Slot()
    def show_last(self):
        if self.last is None or self.closing:return
        self.unread=True
        if self.dialog is not None and not isValid(self.dialog):self.dialog=None
        host=QApplication.activeModalWidget() or self.owner
        if self.dialog is None:
            self.dialog=ProblemDialog(host)
            self.dialog.finished.connect(self.acknowledged)
            self.dialog.destroyed.connect(self.dialog_destroyed)
        elif self.dialog.parentWidget()!=host:self.dialog.setParent(host,Qt.Dialog)
        self.dialog.display(self.last,self.last.report(self.context()))
        self.update_queue_label()
        # show(), not exec(): workers and the Qt loop continue; acknowledging an
        # error never executes or retries an operation.
        self.dialog.show();self.dialog.raise_();self.dialog.activateWindow()

    @Slot()
    def dialog_destroyed(self):
        self.dialog=None
        if not self.closing and self.last and self.unread:QTimer.singleShot(0,self.show_last)

    def update_queue_label(self):
        if self.dialog:
            count=len(self.pending)+self.overflow
            self.dialog.queued.setText(f'Ещё сообщений: {count}. Покажем их после этого.' if count else '')

    @Slot(int)
    def acknowledged(self,*_):
        if self.closing:return
        self.unread=False
        if self.pending:
            self.last=self.pending.pop(0)
        elif self.overflow:
            from ..problems import problem
            self.last=replace(problem(RuntimeError(f'{self.overflow} additional errors; see runtime log'),'notifications',
                state='Результаты записей нужно проверить по списку сцен. Автоматических повторов не было.'),
                title='Есть ещё сообщения',message=f'Поступило ещё {self.overflow} сообщений об ошибках.',
                action='Скопируй отчёт и журнал для помощи: исходные причины сохранены в журнале приложения.')
            self.overflow=0
        else:return
        self.unread=True
        self.changed.emit(self.last)
        QTimer.singleShot(0,self.show_last)

    @Slot(str)
    def recovered(self,operation):
        self.seen={key for key in self.seen if key[0]!=operation}

    @Slot(str)
    def resolve(self,operation):
        self.recovered(operation)
        self.resolved.emit(operation)

    def shutdown(self):
        self.closing=True
        self.unread=False
        self.pending.clear();self.overflow=0
        if self.dialog is not None and isValid(self.dialog):self.dialog.close()
        self.dialog=None
