# Анимации: optional asset v1 type 2, firmware 1.6/1.7

Firmware1.8 добавляет совместимый encoding1 (RLE/изменённые области), capability
bit7. Старый encoding0 ниже остаётся читаемым и побайтово прежним. Новый GUI
автоматически выбирает оптимизацию для поддерживающей платы и показывает
фактический размер/экономию в окне GIF. См. [расширение и измерения](animation_compression.md).

Пользователь согласовал оба профиля: компактный и полный экран. Static type 1
остаётся побайтово прежним. Это расширение asset **v1**, не GIF decoder на ESP32
и не asset/protocol v2. Firmware объявляет поддержку capability byte 7 bit 5.

## Создание и просмотр

Окно выбора кадров показывает число выбранных кадров, точный размер будущего
RGB565 asset и его округлённый расход квоты библиотеки. Диапазон, шаг и профиль
сразу обновляют расчёт, полосу заполнения, доступное место и остаток либо величину
превышения. При замене учитывается размер старой сцены в итоговой библиотеке,
но для безопасной записи требуется место под новую копию рядом со старой.
Если выбранные кадры не помещаются, «Подготовить» недоступна и указано, что нужно
сократить диапазон, увеличить шаг или выбрать меньший профиль. Без подключённой
платы проверяется только размер файла; доступное место явно обозначено неизвестным.
Подсчёт кадров выполняется в фоне. Перед upload место повторно проверяется.
Бюджет декодирования32 MP применяется к выбранным кадрам, а не ко всему ролику.
В RAM сохраняются только выбранные исходные кадры; пропущенные кадры последовательно
обрабатываются для GIF disposal и длительности, но не сохраняются отдельными копиями.

Добавь GIF через «+ добавить» или drag/drop. В диалоге выбери:

| Профиль | Минимальное время кадра | Размер одного RGB565 кадра | Максимум кадров в 820 KiB |
| --- | ---: | ---: | ---: |
| 96×96 | 170 мс (до ~6 FPS) | 18432 Б | 45 |
| 128×128 | 250 мс (до 4 FPS) | 32768 Б | 25 |
| 320×172 | 350 мс (до ~3 FPS) | 110080 Б | 7 |

Окончательный fit зависит от квоты библиотеки и физического free со служебным запасом160 KiB. Старый A/B файл не
считается заранее освобождённым. Full profile медленнее, на LCD возможна видимая
полосная перерисовка/tearing: без double buffer нельзя обещать мгновенный swap.
Preview показывает точные пиксели и длительности, но не симулирует физический SPI scan.

«Настройки GIF…» меняют профиль, диапазон и шаг. Длинные файлы не обрезаются
автоматически; до дорогостоящего render показывается превышение числа кадров/
820 KiB. При явном шаге N длительности пропущенных кадров суммируются в выбранный
кадр, затем ограничиваются минимумом профиля. Быстрый исходный GIF может замедлиться;
итоговые кадры/длительность/байты показаны до upload. Loop бесконечный.

GIF input: до 32 MiB, canvas до 4 MP, 2–65535 исходных кадров, суммарно до 32 MP.
Pillow выполняет disposal/transparency composition. В прошивку уходят полные
непрозрачные RGB565 frames. APNG/animated WEBP пока не поддерживаются.

Компактный кадр перемещается внутри LCD с ограничением по границам; FIT/FILL,
масштаб и фон применяются внутри выбранной области. В full profile сдвиг и масштаб
применяются с обрезкой внутри 320×172. Сохранённую анимацию можно редактировать
из RAM: преобразование применяется ко всем кадрам, длительности/профиль сохраняются.
Исходный GIF и обрезанные части на плате не хранятся. Для смены профиля после
сохранения выбери исходный GIF через «Заменить картинку…».

Preview выбранных device scenes работает без USB reads после prefetch. RGB565
decode выполняется в image worker, проигрывание QTimer — в UI; при скрытии окна
таймер останавливается. USB identity guards отбрасывают stale decoded preview.
Библиотека .fclip сохраняет исходные asset bytes и проигрывает preview offline.

## Binary layout

24-байтный HCAS header из [asset_format.md](asset_format.md): version=1, type=2,
pixel_format=1, reserved=0, width 1–320, height 1–172. Payload CRC охватывает весь
payload, header CRC — первые 20 байт. Максимум **всего файла** 839680 байт.
Все числа little-endian.

Payload prefix (16 байт):

| Offset | Bytes | Field |
| --- | ---: | --- |
| 0 | 2 | frame_count, 2–65535 |
| 2 | 1 | loop_mode=0, infinite |
| 3 | 1 | reserved=0 |
| 4 | 2 | origin_x, i16, 0…320-width |
| 6 | 2 | origin_y, i16, 0…172-height |
| 8 | 4 | frame_bytes=width×height×2 |
| 12 | 4 | reserved=0 |

Затем count записей по 12 байт: offset u32 от начала payload, duration_ms u32,
frame_crc32 u32. Первый offset=16+count×12, следующий увеличивается ровно на
frame_bytes. После таблицы — полные независимые row-major RGB565 frames.
Нет holes, overlaps, trailing data. Payload size=16+count×(12+frame_bytes).
Duration до 60000 мс, минимум: 170 при width/height≤96, 250 при обоих≤128,
иначе 350. Header/index/CRC/bounds проверяются до A/B manifest commit и при
открытии сцены; frame pixels не буферизуются целиком.

## Firmware/storage/USB

Manifest v1 type=2, dimensions одного кадра, checksum всего payload. Файл открыт
на время playback; в RAM только одна запись индекса (валидация порциями32 записи) и существующий DMA strip5120 Б.
На входе в сцену очищается фон, далее кадры обновляют свою область без очистки
экрана. Tick выводит максимум один кадр, не накапливает catch-up queue. USB/main loop
обслуживается между кадрами с yield хотя бы один RTOS tick. Flash по кадрам не пишется.

Upload BEGIN/CHUNK/END сохраняет A/B безопасность. Старая анимация продолжает
читать свой committed slot до успешного END; temporary upload её не заменяет.
Невалидный END отклоняется. Плейлист при upload/delete/manual activation
останавливается как раньше. Dwell плейлиста независим от длительностей кадров;
вход в animation scene запускает первый кадр, затем loop до следующей сцены.
После RESET активная анимация начинается с первого кадра. Ошибка чтения даёт fallback.

Optional commands под capability bit 5:

- GET_ASSET_INFO (20): ID u32 + expected payload CRC u32; ACK actual file bytes u32.
  Нужно до READ_SCENE для type2; static length по-прежнему вычисляется по геометрии.
- UPLOAD_BEGIN_TYPED (21): тот же payload ID/file bytes/file CRC, что у BEGIN (5).
  Новые hosts используют 21 при bit5. Старый BEGIN (5) отклоняет замену ID с type2
  через BAD_STATE, защищая от незаметного overwrite неподдерживаемым host.
- READ_SCENE (11) сохраняет framing/offset/count/ACK, читает реальные file bounds
  ≤820 KiB. При offset0 проверяется весь файл. Host проверяет собранные bytes,
  header/payload/frame CRC, dimensions, type и manifest checksum до preview.

Максимум файла в capabilities теперь 820 KiB; static type1 остаётся ≤110104 Б.
Старые команды/статичные assets работают на 1.6. Для animation scenes нужна
новая desktop версия: старый GUI может не прочитать animation payload/cache.
Downgrade firmware ниже1.6 с type2 в manifest не поддерживается; предварительно
сделай экспорт и убери animation scenes поддерживаемой версией. Автоматического
downgrade нет. Flash остаётся 4 MB, LittleFS partition прежняя.

Архив .fclip остаётся version1, strict ZIP/JSON и SHA256; per-asset limit 820 KiB,
whole archive5 MiB, uncompressed assets4 MiB, JSON64 KiB, до206 entries. Старые static collections читаются как прежде.
Restore на firmware без bit5 отклоняется до записи.


Firmware1.7 вводит фиксированный общий бюджет и расширенный список сцен;
см. [вместимость](library_capacity.md). Binary type2 layout остаётся прежним.

## Проверки этапа (история firmware1.6)

Python tests: codec, frame/index CRC, malformed bounds/duration/offsets, file cap,
GIF disposal/range/profile, exact RGB565, immutable cached edit всех кадров,
capability gate, interrupted mock upload, GUI выбор обоих профилей и RAM preview.
Frozen mock smoke включает оба профиля, animation library preview/restore, 0 USB reads.

На плате firmware1.6: реальный GIF подготовлен в128×128 и320×172; upload и exact
readback обоих assets прошли. Повреждённый frame с валидными внешними CRC отклонён
через INVALID_ASSET, старый asset сохранён. Старый BEGIN не перезаписал animation ID.
Прерванный typed upload+программный RESET сохранил compact asset. Все семь прежних
пользовательских assets побайтово совпали с preflight архивом, имена/порядок сохранены.
Пользователь подтвердил оба профиля на LCD и совпадение с preview. Полноэкранная
анимация восстановлена из библиотеки с заменой только тестовой сцены ID8;
после закрытия приложения и аппаратного RESET она снова воспроизводится.
Подтверждённая сцена ID8 оставлена на плате. Два тестовых архива добавлены
в локальную библиотеку; пользовательские сцены 1–7 не заменялись.

100 desktop + 9 tools tests прошли; extracted ZIP smoke: 16 checks, 0 asset reads,
6.094 секунды. Firmware.bin 362992 байта; ESP-IDF5.5.3, Flash4 MB, partition layout
не менялись. После подготовки compact test LittleFS total/free=2031616/360448.

C foundation tests дополнены type2/frame CRC. Native GCC/Clang отсутствует,
C host suites не выполнены; firmware build прошёл. Зависимости не обновлялись.
