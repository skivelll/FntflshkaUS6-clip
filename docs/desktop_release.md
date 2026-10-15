# Windows portable application

Эта фаза готовит существующий scene manager к обычному запуску на Windows.
Firmware 1.2, asset v1, protocol v1 и Flash/LittleFS layout не меняются.

## Сборка из исходников

В корне репозитория, нативно на Windows x64 с Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .\desktop
.\.venv\Scripts\python.exe -m pip install -r .\desktop\requirements-build.txt
.\.venv\Scripts\python.exe -m unittest discover -s desktop/tests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/build_desktop.py
```

Если `.venv` уже создана, её повторное создание не требуется. Build requirements
отдельны от runtime и firmware tools. PyInstaller 6.22.3 и его hooks закреплены;
существующие PySide6/Pillow/pyserial и ESP-IDF не обновляются.
Используется [PyInstaller spec с onedir/windowed](https://pyinstaller.org/en/stable/spec-files.html):
Qt plugins, Python runtime и QSS попадают в переносимую папку, консоль не открывается.
UPX не используется, прав администратора и установки IDE не требуется.

Результат:

- `desktop/dist/FntflshkaUS6 clip/FntflshkaUS6 clip.exe`;
- соседняя `_internal` с runtime;
- README и `build-info.json` с версиями, исходным commit/dirty flag и SHA256 exe;
- `desktop/dist/FntflshkaUS6-clip-windows-x64.zip` для переноса всей папки.

Не переноси только exe и не запускай его внутри ZIP. Артефакты `build/dist`
игнорируются Git. Сборка Windows выполняется на Windows; исходники Python
по-прежнему доступны на Linux. Цифровая подпись и installer пока не реализованы.

## Проверка готового exe

```powershell
& '.\desktop\dist\FntflshkaUS6 clip\FntflshkaUS6 clip.exe' --mock --smoke-test "$PWD\desktop\build\smoke.json"
```

`--smoke-test` требует `--mock`, проверяет импорт PNG с Unicode-путём, FIT/FILL,
точный RGB565 preview, две загрузки, кэш, имя/порядок, переподключение и удаление.
GUI работает асинхронно, отчёт содержит `ok`, checks и event-loop ticks; приложение
закрывается само. Discovery и настоящий serial transport в этой проверке запрещены.
Если запускаешь из PowerShell, дождись завершения процесса либо используй
`Start-Process -Wait` перед чтением отчёта (windowed exe может вернуть управление сразу).

Автоматическая проверка именно ZIP, а не рабочей папки исходников:

```powershell
.\.venv\Scripts\python.exe tools/verify_desktop.py
# Дополнительно с реальной платой, только чтение:
.\.venv\Scripts\python.exe tools/verify_desktop.py --device
```

Скрипт распаковывает ZIP во временную папку с Unicode-именем, проверяет SHA256,
запускает exe с cwd вне репозитория, без Python/venv в PATH и без PYTHONHOME/
PYTHONPATH/внешних Qt plugin paths. Python нужен только проверяющему скрипту;
запущенный exe пользуется своим runtime. Отчёты — в `desktop/build/verification`.

С подключённой платой и закрытым другим экземпляром/monitor:

```powershell
& '.\desktop\dist\FntflshkaUS6 clip\FntflshkaUS6 clip.exe' --device-check "$PWD\desktop\build\device-check.json"
```

Эта диагностическая проверка только читает HELLO/capabilities/manifest/assets,
выбирает все previews из RAM, закрывает и повторно открывает USB. Проверяет,
что snapshot, LittleFS free, сцены и ACTIVE не изменились и повторных asset reads
не было. Не выполняет upload, delete, rename, activate, flash или форматирование.
При отсутствии устройства завершается с ошибкой по таймауту 180 секунд.
Отчёт содержит USB identity и scene metadata; он предназначен для локальной
диагностики и не коммитится. Программное переподключение не доказывает физическое
отключение USB, LCD или hardware RESET: эти проверки требуют пользователя.

## Поведение при обычном запуске

Приложение хранит verified assets в RAM до закрытия; selection не читает USB.
Начальная загрузка имеет progress и блокирует конфликтующие операции. При
повторном подключении той же платы кэш переиспользуется, при другой identity
сбрасывается. Добавление/замена обновляет только изменившиеся assets.

«Переподключить» работает в serial worker. Занятый COM-порт, timeout, нехватка
памяти и ошибки записи получают разные сообщения. После обрыва upload приложение
не повторяет передачу: результат не подтверждён, надо проверить обновлённый список.
Нельзя обещать, что upload не сохранился, если ответ потерялся после commit.
Автоматический recovery по-прежнему разрешён только для ранее идентифицированного
порта с той же VID/PID/непустой serial identity в текущем сеансе.

`QLockFile` в user-data блокирует второй экземпляр до USB-доступа, освобождается
при нормальном завершении и использует определение завершившегося процесса для
stale lock. Demo и real используют разные lock/settings. Это защита экземпляров
приложения; другие serial программы всё равно могут занять порт.

Настройки окна/изображений и последней папки лежат в `settings.ini`, журналы
с ротацией — в `logs`. Они находятся в `QStandardPaths.AppLocalDataLocation`,
не рядом с exe и не в репозитории. Нет сохранённых USB trust, assets или исходных
картинок в настройках. Некорректные значения настроек игнорируются.

## Validation

Проверено 2026-10-15 на Windows 11 10.0.26200 x64, Python 3.12.10:

- 49 desktop tests + 8 tools tests: PASS (включая второй процесс до USB-доступа,
  настройки, occupied port, обрыв upload без повтора и сохранение предупреждения).
- `pip check`, `compileall`, `git diff --check`: PASS.
- PyInstaller 6.22.3, hooks 2026.8; runtime PySide6 6.10.3, Pillow 12.3.0,
  pyserial 3.5 остался прежним. Сборка unsigned, portable onedir.
- ZIP 52,807,550 bytes; exe 2,722,805 bytes; папка 128,796,079 bytes.
- SHA256 exe: `c6bafe6ca2a904382f8a75eb0ffa3957eed15eee073ec54f71833dbfea3a6c68`.
- Распакованный ZIP вне repo, в Unicode-папке, без Python в PATH: PASS.
- Native Windows Qt smoke: PASS, 3.250 s, 83 event-loop ticks;
  все 8 checks (включая replacement) прошли. Выбор preview,
  metadata и reconnect не вызывали чтения assets; upload сам заполнял кэш.
  Native screenshot проверен: русский текст и layout отображаются корректно.
  Offscreen Windows backend без системных fonts показывает квадраты в screenshot,
  поэтому для визуальной проверки portable exe используется native backend.
- Реальная плата через распакованный exe: firmware 1.2, COM3,
  `303a:1001`, serial `AC:EB:E6:3E:94:90`, landscape 320×172.
- Шесть прежних сцен ID 1–6, ACTIVE 6. LittleFS total/free
  `2031616/1314816`. Read-only проверка 53.875 s; 6 asset reads при старте,
  после выбора всех previews и программного reconnect всё ещё 6.
  Snapshot, CRC, порядок, ACTIVE и free не изменились.
- Пользователь подтвердил обе физические проверки через обычный portable GUI:
  новая PNG успешно загружена, картинка на LCD совпала с preview;
  после физического отключения/подключения USB восстановились список сцен
  и быстрый выбор preview. Эти результаты подтверждены пользователем,
  отдельно от автоматической read-only диагностики.

Firmware в этой фазе не менялась и не прошивалась. C host tests относятся
к неизменённой firmware; ограничение отсутствующего native C11 compiler сохраняется.
