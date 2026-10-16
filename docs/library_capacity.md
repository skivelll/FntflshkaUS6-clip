# Фиксированная вместимость — firmware 1.7

Пользователь не задаёт квоту или число слотов. Максимум библиотеки и одиночного
asset: **820 KiB (839680 байт)**. Flash остаётся 4 MB, LittleFS — 1984 KiB.

## Расчёт и проверка LittleFS

Худший случай — один asset на всю библиотеку: безопасная замена хранит старый
и новый файлы одновременно. При T=1984 KiB, служебном бюджете S=160 KiB:
L=(T-S)/2=912 KiB. Вычитаем 10% из L, а не из Flash: 820.8 KiB. Округляем вниз
до блока 4 KiB: **820 KiB**. Это инженерный предел с запасом, не гарантия против
любого физического повреждения Flash или питания.

Модель множества файлов/большого manifest показала metadata overhead порядка
132 KiB на пике. Поэтому предварительный S=128 KiB увеличен до160 KiB,
а предварительный лимит832 KiB заменён на820 KiB.
Библиотека учитывает каждый живой файл, округлённый вверх до4096 Б. Поэтому
даже inline asset учитывается в квоте минимум одним блоком (это консервативный
учёт, не фактически выделенные LittleFS блоки), и теоретически помещается
до **205** файлов. Столько же записей поддерживает manifest; ID1–65535,
ноль — fallback. Это защитная граница, выведенная из бюджета, вместо восьми
продуктовых слотов. Число кадров ограничено размером файла и uint16 формата.

Перед upload дополнительно проверяется фактический free >= rounded_new_file
+160 KiB. Квота не заменяет проверку диска. CTZ overhead, директории,
manifest/playlist и временные записи покрываются служебным бюджетом и запасом.

Windows-модель LittleFS2.11 / disk2.1 совпадает с firmware: 496×4096 Б,
read/prog128, cache512, lookahead128, block_cycles512. Полная квота проверена
для1/8/205 файлов, включая A/B replacement и atomic manifest commit.

| Файлов | Manifest | Пик занято | Свободно на пике | Remount snapshots |
| --- | ---: | ---: | ---: | ---: |
| 1 | 243 Б | 1708032 Б | 323584 Б | 10 |
| 8 | 1699 Б | 1011712 Б | 1019904 Б | 13 |
| 205 | 43277 Б | 978944 Б | 1052672 Б | 14 |

Все37 снимков persisted bytes смонтированы без форматирования: manifest целый
старый либо целый новый, выбранные и посторонние файлы совпали побайтово.
Это RAM-backed модель, **не физический brownout ESP32** и не исполнение storage.c.
Изолированный запуск без production зависимости:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-deps --target .pio/capacity-test-runtime -r tests/requirements-storage.txt
.\.venv\Scripts\python.exe tools/verify_storage_budget.py --runtime .pio/capacity-test-runtime --report .pio/capacity-littlefs-model.json
```

## Commit и существующие данные

Новый файл пишется отдельно, CRC/index проверяются, затем файл перемещается
в неактивный A/B и атомарно сохраняется manifest. Только после commit удаляются
неупомянутые A/B файлы. После RESET cleanup повторяется. Обрабатываются лишь
канонические scenes/NNNNa.bin/NNNNb.bin с ID1–65535. Если manifest отсутствует/
повреждён либо живой asset невалиден, cleanup ничего не удаляет. Форматирование
существующего хранилища запрещено; пользовательские сцены не удаляются автоматически.
Повреждённый manifest либо его отсутствие при существующих файлах блокирует
также последующие content/manifest writes до восстановления: нельзя заменить
его пустым trusted manifest и удалить recovery-файлы при следующем boot.

Старая библиотека сверх квоты остаётся читаемой и доступной для удаления.
Upload сверх квоты запрещён, кроме замены, которая строго уменьшает учтённый
размер библиотеки. Равная по размеру правка сверх квоты запрещена: GUI объясняет,
что сначала нужно освободить место. Обновление не меняет качество старых assets.

## USB protocol v1, capability bit6

Framing/CRC/520-byte payload не изменены. Старые команды сохранены. Legacy
LIST_SCENES возвращает BAD_STATE при count>8, вместо переполнения/усечения.
Все числа little-endian, ответы ниже — ACK data без echoed command byte.

- **22 LIST_SCENES_PAGE**: request offset u16. Reply total u16, next_offset u16,
  active_id u32, revision u32, count u8, затем legacy scene records, дополненные
  actual_file_size u32 после filename. Reply<=519 Б. Host проверяет progress,
  total/revision и уникальность ID, имена читает через12.
- **23 GET_LIBRARY_POLICY**: пустой request. Reply24 Б: limit u32, used u32,
  service_reserve u32, allocation_block u32, max_records u16, max_id u16,
  revision u32. Used — сумма округлённых живых файлов.
- **24 METADATA_BEGIN**: kind u8 (0 reorder, 1 playlist), count u16, revision u32;
  для kind1 ещё enabled u8, effect u8, interval_ms u32. Только RAM transaction;
  kind0 count совпадает с manifest.
- **25 METADATA_CHUNK**: offset u16 (индекс), затем id u32/checksum u32 пары.
  Последовательные части, host отправляет до60 записей за request. Неверный
  offset/length отменяет transaction; частичные данные не меняют Flash.
- **26 METADATA_COMMIT**: пустой request. Проверяет completeness/revision,
  уникальность и checksums, затем один atomic commit reorder либо playlist.
  Новый BEGIN заменяет брошенную RAM transaction. Обрыв не повторяется автоматически.
- **27 GET_PLAYLIST_PAGE**: request byte_offset u16. Reply total_bytes u16,
  byte_offset u16, full_record_crc32 u32, до500 байт extended record.
  Record: version=2 u8, enabled u8, effect u8, reserved=0 u8, interval_ms u32,
  count u16, затем count×id u32. Host проверяет полный CRC.
- **28 BENCH_MANIFEST**: пустой request, только явная read-only диагностика.
  Reply8×u32: schema1, record_count, encoded_json_bytes, elapsed_us, heap_before,
  heap_after, largest_before, minimum_free_heap. Создаёт синтетические205 записей
  с максимально экранируемыми48-byte titles, проверяет C encode/parse в RAM;
  файлы/настоящий manifest/сцены не меняет. Не запускается автоматически.

Revision — CRC32 упорядоченных id/order/checksum/filename/title; runtime ACTIVE
не входит, чтобы timer не делал чтение нестабильным. Это stale guard, не
аутентификация. On-disk playlist принимает legacy42+4 Б и extended record+CRC;
новые записи используют extended record. Legacy GET_PLAYLIST отклоняет
неподдерживаемые count/ID. Asset и manifest остаются version1.

Новые лимиты/extended playlist требуют firmware1.7 и актуальный desktop.
Автоматический downgrade запрещён: старая firmware не поддерживает большой
manifest, >8 сцен, extended playlist или файлы/число кадров сверх её лимитов.
Совместимость означает сохранение старых файлов/команд и работу нового host
со старой firmware в пределах объявленных ею capabilities.

## Анимации, desktop, архивы

Type2 layout прежний; frame_count2–65535, максимум определяется размером файла.
Firmware читает одну12-байтную запись с Flash вместо массива128 элементов.
Вычисление размера использует uint64_t до доступа к payload.
Raw максимум: 25 кадров128×128, 45 кадров96×96, 7 кадров320×172. FPS прежний;
сжатие ещё не реализовано.

Preload raw assets остаётся RAM-only, выбор сцены не перечитывает USB.
Animation preview хранит decoded pixels исходного размера и максимум два LCD
canvas; RGB565 conversion в worker. GIF input:32MiB/4MP canvas/32MP суммарно/
uint16 count. GUI показывает квоту отдельно от физического free.
Архивы принимают >8 сцен и ID до65535, asset до820KiB; отдельный offline budget
4MiB uncompressed сохраняет чтение legacy collections.

Python tests: >8 сцен, порционные reorder/playlist, полная квота и замена,
отказ до записи, stale/partial transactions, >512KiB файл, >128 кадров,
bounded preview, legacy overbudget и restore. Аппаратные результаты фиксируются
отдельно после выполнения; build не доказывает LCD/RESET.

## Аппаратная проверка firmware 1.7

На ESP32-C6 (USB serial AC:EB:E6:3E:94:90) проверены 205 реальных сцен с максимально
экранируемыми 48-byte именами: полная квота, отказ206-й записи, безопасная замена,
порядок и плейлист205 записей, RESET и побайтовое чтение всех файлов. Проверены
загрузка и A/B замена файла770684 байта с7 полноэкранными кадрами. Более ранний
тест подтвердил1000 маленьких кадров, отказ повреждённого asset и сохранность
предыдущего файла после прерванной загрузки.

Первый boot с205 сценами выявил task watchdog: долгий cleanup не отдавал CPU.
В циклы добавлены vTaskDelay(1), а при отсутствии orphan файлов полная проверка
всех assets не запускается. Watchdog сохранён. Повторный boot: USB готов примерно
через1.3 секунды; все205 файлов, порядок и плейлист сохранены, reset loop отсутствует.
Read-only C manifest diagnostic:205 записей,42460 JSON bytes,167023 us;
heap до/после338568 bytes, minimum free109592 bytes.

Firmware.bin368512 bytes; Flash4 MB и partitions не изменены. После разрешённой
очистки нагрузочных fixtures оставлен визуальный ID9 (32792 bytes, charge36864).
LittleFS total2031616/free1978368 bytes. Пользовательские сцены были самостоятельно
удалены пользователем во время паузы; прежняя проверенная резервная копия сохранена.
Пользователь подтвердил совпадение preview с LCD визуального теста ID9 и сохранность
той же картинки после аппаратного RESET. Фаза завершена 2026-10-15.
110 desktop +9 tools tests прошли; последняя portable сборка прошла isolated
smoke и read-only device/cache/reconnect checks. Визуальный тест ID9 оставлен на плате.

Native C11 host tests расширены, но не запущены: на машине нет C11 компилятора.
Это ограничение не заменяется моделью LittleFS; реальные C manifest/USB/storage
пути дополнительно проверены на плате. Физическое отключение питания в момент
commit не тестировалось;37 модельных remount snapshots не являются таким тестом.
