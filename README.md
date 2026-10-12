# FntflshkaUS6 clip firmware foundation

## User content over USB

Firmware is configured for **4 MB Flash**. Its [partition layout](docs/storage_layout.md)
has a 2 MB factory app and a 1984 KiB LittleFS partition. The firmware mounts
LittleFS at boot, validates `manifest.json` and the active asset, then displays
it from Flash through the existing 5120-byte LCD strip buffer. Missing or bad
content falls back to the compiled ART/TEXT/ANIMATION demo.

The [asset format](docs/asset_format.md) is static RGB565 v1. The
[USB protocol](docs/usb_protocol.md) is framed protocol v1 with CRC32 and
ACK/NACK. Neither uploading an image nor changing the active scene reflashes
firmware. Up to eight scene IDs are accepted; the initial workflow uses ID 1.

Ubuntu tools use the existing system Python packages `pyserial` and Pillow:

```sh
python3 tools/make_asset.py picture.png /tmp/picture.bin
python3 tools/clipctl.py info
python3 tools/clipctl.py upload /tmp/picture.bin
python3 tools/clipctl.py list
python3 tools/clipctl.py activate 1
python3 tools/clipctl.py delete 1
```

Pass `--port /dev/ttyACM0` before a subcommand if auto-discovery fails. Keep
PlatformIO serial monitor closed while using `clipctl`, because one serial port
cannot be opened by both programs at once. The uploaded image is auto-activated
and restored after reset. A/B filenames preserve the previous active file
until the new upload and manifest commit succeed.

Host tests:

```sh
cc -std=c11 -Wall -Wextra -Werror -Isrc \
  -I/tmp/hairclip-platformio/packages/framework-espidf/components/json/cJSON \
  tests/foundation_test.c src/wire.c src/asset_format.c src/protocol.c \
  src/manifest.c src/upload_guard.c \
  /tmp/hairclip-platformio/packages/framework-espidf/components/json/cJSON/cJSON.c \
  -lm -o /tmp/fntflshkaus6-foundation-test
/tmp/fntflshkaus6-foundation-test
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The LittleFS component is pinned by `src/idf_component.yml` and
`dependencies.lock`. On this machine, ESP-IDF's component cache needs a
writable temporary location; use `XDG_CACHE_HOME=/tmp/hairclip-cache` with
the PlatformIO commands below. A fresh `/tmp` requires restoring the
PlatformIO toolchain before rebuilding.

Минимальная прошивка для встроенного LCD Waveshare ESP32-C6-LCD-1.47.

Параметры подключения, смещение экрана и файл `Vernon_ST7789T.c/.h` взяты из
[официального архива Waveshare](https://files.waveshare.com/wiki/ESP32-C6-LCD-1.47/ESP32-C6-LCD-1.47-Demo.zip),
`ESP-IDF/ESP32-C6-LCD-1.47-Test/main/LCD_Driver/`. Инициализация панели
сохранена в `src/display.c`. Аккумулятор, SD-карта и внешнее питание не нужны.

`src/display.c/.h` отвечает за LCD, подсветку, поворот на 90° и отрисовку в
логических координатах 320×172. Драйвер Waveshare `Vernon_ST7789T` не изменён.
`src/assets.c/.h` описывает изображения `ImageAsset` с индивидуальными
`width`/`height` и RGB565-данными во Flash. `src/modes.c/.h` хранит текущий
режим и управляет переходами. `src/main.c` запускает дисплей и периодически
вызывает `modes_update()`.

Режим `ART` показывает широкий рисунок 256×128 и pixel-art сердце 24×24 с
масштабом 4, по 2,5 секунды каждый. `TEXT` показывает `FNTFLSHKAUS6` и
`CLIP` 5 секунд. `ANIMATION` двигает сердце слева направо с масштабом 3;
позиция зависит от прошедшего времени, а обновление запрашивается каждые 100 мс.
После 5 секунд режим снова становится `ART`.
Смена режимов сосредоточена в `modes_next()`; позднее её можно будет вызывать
из обработчика кнопки MODE вместо таймера в `modes_update()`.

Тестовые пиксели генерируются скриптом `tools/generate_assets.py` в
`src/generated_assets.inc`. Для нового арта добавьте в генератор массив RGB565,
затем объявите `ImageAsset` с его шириной и высотой в `src/assets.c` и добавьте
его в `art_scenes` с целочисленным масштабом. Масштаб должен помещаться в
320×172; `show_art()` центрирует изображение по фактическому размеру. Дисплей
рисует напрямую через один DMA-буфер 320×8 пикселей (5120 байт), без полного
framebuffer. `draw_image()` выводит исходный размер, `draw_image_scaled()` —
целочисленный масштаб. Оба метода обрезают область за пределами экрана.
В serial log при запуске печатаются размеры и объём каждого asset в RGB565:
256×128 занимает 65536 байт, 24×24 — 1152 байта.

На Ubuntu 26.04 системный Python 3.14 несовместим с одной из зависимостей
ESP-IDF 5.5.3. Для этой сборки использован Python 3.13 в `/tmp/hairclip-python`,
PlatformIO Core в `.venv313` и скачанные toolchain/ESP-IDF в
`/tmp/hairclip-platformio`. Каталоги в `/tmp` исчезнут после перезагрузки.

```sh
export PLATFORMIO_CORE_DIR=/tmp/hairclip-platformio
export PLATFORMIO_DISABLE_TELEMETRY=1
export XDG_CACHE_HOME=/tmp/hairclip-cache
.venv313/bin/python -m platformio run
.venv313/bin/python -m platformio run -t upload
.venv313/bin/python -m platformio device monitor -p /dev/ttyACM0 -b 115200
```

Подсветка установлена на 50% согласно рекомендации Waveshare. В serial monitor
печатаются режим, номер арта или номер кадра.

Проверка таймеров и переходов режимов на ПК:

```sh
cc -std=c11 -Wall -Wextra -Werror -Itests/stubs -Isrc \
  tests/modes_test.c src/modes.c src/assets.c -o /tmp/fntflshkaus6-modes-test
/tmp/fntflshkaus6-modes-test
```

При загрузке esptool определил установленный чип как ESP32-C6FH8 с 8 МБ flash,
хотя документация Waveshare для этой модели указывает 4 МБ. Для данного теста
сохранена конфигурация 4 МБ; образ занимает только начало flash. Перед проектом,
которому нужно больше памяти, следует уточнить ревизию платы.

