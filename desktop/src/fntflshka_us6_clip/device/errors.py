"""Russian messages without leaking tracebacks into the interface."""
from ..core.protocol import DeviceNack


def port_busy(error):
    message = str(error).lower()
    return (isinstance(error, PermissionError) and getattr(error, "winerror", None) in (None, 5, 32)) or any(
        hint in message for hint in ("access is denied", "access denied", "permission denied",
                                     "отказано в доступе", "being used by another process"))


def user_error(error):
    if isinstance(error, DeviceNack):
        return {
            1: "Данные повредились при передаче. Попробуй ещё раз.",
            3: "Версия протокола устройства не поддерживается этим приложением.",
            4: "На заколке не хватает памяти. Удали ненужную сцену и повтори действие.",
            5: "Заколка отклонила файл. Подготовь изображение заново.",
            7: "Не удалось записать сцену в память заколки. Проверь подключение и список сцен.",
            9: "Предыдущая загрузка не завершена. Переподключи заколку по USB.",
            10: "Сцена больше не найдена. Дождись обновления списка и выбери её заново.",
            11: "Ошибка памяти заколки. Подробности записаны в журнал; память не форматировалась.",
        }.get(error.code, "Заколка не смогла выполнить действие. Подробности в журнале.")
    if port_busy(error):
        return "COM-порт занят или доступ запрещён. Закрой serial monitor и другие приложения для платы, затем нажми «Переподключить»."
    if isinstance(error, InterruptedError):
        return "Операция остановлена. После подключения проверь список сцен перед повторной загрузкой."
    if isinstance(error, TimeoutError):
        return "Заколка не отвечает. Дождись восстановления связи или переподключи USB-кабель."
    if isinstance(error, OSError):
        return "Связь с заколкой потеряна. Подключи её по USB-C."
    if isinstance(error, ValueError):
        message = str(error)
        if any("А" <= char <= "я" or char in "ёЁ" for char in message):
            return message
        return "Не удалось проверить данные сцены или устройства. Попробуй подключиться заново."
    return "Что-то пошло не так. Подробности записаны в журнал."
