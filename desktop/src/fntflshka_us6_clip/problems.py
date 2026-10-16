"""Qt-independent, bounded support reports. Reports never contain asset bytes."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import errno
import json
import platform
import traceback
from pathlib import Path
from .core.protocol import DeviceNack
from .device.errors import port_busy

READ_ONLY = {'connection','image','preview','library','export','validation','startup','settings'}
OPERATIONS = {'connection':'Подключение заколки','image':'Подготовка изображения',
              'preview':'Просмотр сцены','upload':'Загрузка сцены','scene':'Изменение сцены',
              'library':'Локальная библиотека','export':'Экспорт коллекции','restore':'Восстановление коллекции',
              'playlist':'Настройки плейлиста','validation':'Проверка настроек','startup':'Запуск приложения','settings':'Сохранение настроек приложения'}


def safe_context(value,depth=0):
    if isinstance(value,(bytes,bytearray,memoryview)):return {'binary_bytes':len(value)}
    if depth>8:return '<depth limit>'
    if isinstance(value,dict):return {str(k):safe_context(v,depth+1) for k,v in list(value.items())[:256]}
    if isinstance(value,(list,tuple)):return [safe_context(v,depth+1) for v in value[:256]]
    if isinstance(value,str):return value[:4096]
    return value


@dataclass(frozen=True)
class Problem:
    operation: str
    title: str
    message: str
    action: str
    state: str
    exception_type: str
    exception_message: str
    trace: str = ''
    context: dict = field(default_factory=dict)
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def key(self):
        target=next((self.context.get(k) for k in ('scene_id','scene_key','request','source_path','file_path','action') if self.context.get(k) is not None),None)
        return (self.operation,self.exception_type,self.exception_message,str(target))

    def report(self, extra=None):
        context=safe_context({**self.context,**(extra or {})})
        text=(f'FntflshkaUS6 clip — diagnostic report\nUTC: {self.created}\n'
              f'OS: {platform.platform()}\nPython: {platform.python_version()}\n'
              f'Operation: {self.operation} / {OPERATIONS.get(self.operation,self.operation)}\n'
              f'Exception: {self.exception_type}: {self.exception_message}\n'
              f'State: {self.state}\nAutomatic write retry: disabled\n'
              f'Traceback / exception chain:\n{self.trace or "No traceback (local validation/discovery state)."}\n'
              f'Context:\n{json.dumps(context,ensure_ascii=False,indent=2,default=str)}')
        home=str(Path.home())
        text=text.replace(home,'<HOME>').replace(home.replace('\\','/'),'<HOME>')
        return text if len(text)<=65536 else text[:65490]+'\n[Report truncated at 64 KiB]'


def problem(error, operation, *, context=None, state=None):
    original=error
    committed=getattr(error,'committed',None)
    if hasattr(error,'error') and isinstance(error.error,Exception):
        error=error.error
    text=str(error)
    lower=text.lower()
    title='Не получилось выполнить действие'
    message='Приложение остановило эту операцию.'
    action='Открой «Подробнее» и скопируй отчёт для помощи.'
    if operation=='connection' and isinstance(error,FileNotFoundError):
        title='Заколка не найдена';message='Приложение пока не видит заколку.'
        action='Подключи её USB-кабелем с передачей данных. Поиск продолжится автоматически.'
    elif port_busy(error):
        title='Подключение занято';message='Другое приложение использует подключение к заколке, или Windows запретила доступ.'
        action='Закрой другие окна приложения и монитор порта. Затем нажми «Переподключить».'
    elif isinstance(error,InterruptedError):
        title='Операция остановлена';message='Действие не было завершено полностью.'
        action='Проверь список сцен и результат последнего действия перед повтором.'
    elif isinstance(error,TimeoutError):
        title='Заколка не отвечает';message='Ответ от заколки не пришёл вовремя.'
        action='Проверь USB-кабель, переподключи заколку и дождись списка сцен.'
    elif isinstance(error,DeviceNack):
        if error.code==4:
            title='Не хватает места';message='В памяти заколки недостаточно места для безопасной записи.'
            action='Уменьши сцену или освободи место, сохранив нужные сцены в библиотеку.'
        elif error.code in (1,5):
            title='Файл не прошёл проверку';message='Заколка отклонила данные сцены.'
            action='Подготовь картинку заново. Перед повторной записью проверь список сцен.'
        elif error.code==10:
            title='Сцена больше не доступна';message='Выбранной сцены уже нет на заколке.'
            action='Дождись обновления списка и выбери сцену заново.'
        elif error.code==11:
            title='Не удалось прочитать память заколки';message='Заколка сообщила об ошибке хранилища.'
            action='Не очищай память. Скопируй технический отчёт для помощи.'
        else:
            title='Заколка отклонила действие';message='Устройство не смогло выполнить выбранную операцию.'
            action='Обнови подключение и проверь результат. Если ошибка повторяется, скопируй отчёт.'
    elif isinstance(error,OSError):
        if operation in ('library','export','startup','settings'):
            title='Не удалось открыть или сохранить файл';message='Файл или папка недоступны на компьютере.'
            action=('Освободи место на компьютере и повтори сохранение.' if error.errno==errno.ENOSPC else
                    'Проверь путь, свободное место и разрешение на запись. Выбери другую папку при экспорте.')
        elif operation in ('image','preview'):
            title='Не удалось открыть картинку';message='Файл недоступен или его не получается прочитать.'
            action='Выбери файл заново или попробуй другую картинку.'
        else:
            title='Связь с заколкой потеряна';message='USB-подключение прервалось.'
            action='Подключи кабель заново и дождись списка сцен.'
    elif isinstance(error,ValueError):
        if operation=='connection':
            title='Устройство не распознано';message='Подключённое устройство не ответило как FntflshkaUS6 clip.'
            action='Проверь, что подключена нужная заколка. Если это она, скопируй отчёт для помощи.'
        elif operation in ('image','preview'):
            title='Не удалось подготовить картинку';message='Файл или выбранные настройки не прошли проверку.'
            action='Для GIF сократи диапазон, увеличь шаг или выбери меньший профиль. Для картинки выбери другой файл. Причина — в «Подробнее».'
        elif operation in ('library','export'):
            title='Коллекция не прошла проверку';message='Архив повреждён, имеет неподходящий формат или недоступные данные.'
            action='Выбери другую резервную копию. Исходный архив не удалялся.'
        elif any(word in lower for word in ('мест','памят','kiб','kib','space')):
            title='Не хватает места';message='Выбранные данные не помещаются в доступный объём.'
            action='Уменьши сцену или освободи место. Подробный расчёт доступен рядом с настройками.'
        elif any(word in lower for word in ('измен','stale','checksum')):
            title='Сцена или подключение изменились';message='Прежний выбор больше не соответствует состоянию заколки.'
            action='Дождись обновления списка и выбери сцену заново.'
        else:
            title='Настройки не подходят';message='Действие остановлено при проверке данных.'
            action='Проверь выбранные сцены и настройки. Точная причина доступна в «Подробнее».'
    if state is None:
        if committed is not None:
            state=f'Подтверждённые сцены: {list(committed)}. Результат шага со сценой {getattr(original,"attempted",None)} нужно проверить. Остальные шаги не запускались автоматически.'
        elif operation in READ_ONLY:
            state='Эта операция не записывала и не удаляла сцены на заколке.'
        else:
            state='Завершение операции не подтверждено. После подключения проверь сцены или настройки. Автоматического повтора записи нет.'
    details=dict(context or {})
    for name in ('command','sequence','port','errno','winerror'):
        if getattr(error,name,None) is not None:details[name]=getattr(error,name)
    if isinstance(error,DeviceNack):details['nack_code']=error.code
    if committed is not None:
        details.update(committed_ids=list(committed),attempted_id=getattr(original,'attempted',None))
    trace=''.join(traceback.format_exception(type(original),original,original.__traceback__,limit=12))[-16384:]
    return Problem(operation,title,message,action,state,type(error).__name__,text[:4096],trace,details)
