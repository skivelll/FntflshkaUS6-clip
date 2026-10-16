# FntflshkaUS6 clip firmware foundation

## Desktop MVP (Windows)

Нативный GUI **FntflshkaUS6 clip**: автоматическое подключение, PNG/JPG/WEBP,
FIT/FILL, масштаб/позиция/фон, RGB565-preview, предзагрузка сцен в память,
названия/порядок/замена картинок, включение/удаление сцен и загрузка без
перепрошивки firmware. [Запуск, архитектура и ограничения](desktop/README.md).

```powershell
.\.venv\Scripts\python.exe -m pip install -e .\desktop
.\.venv\Scripts\python.exe -m fntflshka_us6_clip
```

После установки запуск без терминала: `.venv\Scripts\fntflshka-clip.exe`.
Для запуска без установленного Python есть переносимая Windows x64 сборка
с `FntflshkaUS6 clip.exe`; [сборка ZIP и проверка](docs/desktop_release.md).
Firmware development setup ниже остаётся отдельным workflow.

[Интерактивный редактор сцен и полоса памяти](docs/image_editor.md) ·
[План следующих фаз](docs/roadmap.md).

[Локальная библиотека, экспорт и восстановление коллекций](docs/library.md)
доступны через кнопку «Библиотека…»; сохранённые коллекции просматриваются без USB.

[Автономный плейлист статичных сцен](docs/playlist.md) на firmware 1.3:
кнопка «Плейлист…», выбранный порядок и общий интервал; работает без приложения.

[GIF-анимации](docs/animation_format.md) с firmware1.6: компактный или полноэкранный
профиль, точный preview, safe upload и автономный loop. Static asset v1 сохранён.

## Windows development (primary workflow)

Use native Windows 10/11, Git for Windows and Python 3.12 or 3.13. Python
3.14 is not validated for this ESP-IDF stack. VS Code and Arduino IDE are not
required. Run these PowerShell commands from the repository root (choose an
installed compatible Python with `py -0p`):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m serial.tools.list_ports -v
.\.venv\Scripts\python.exe tools/clipctl.py info
.\.venv\Scripts\python.exe tools/clipctl.py list
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m platformio run
```

PlatformIO uses its permanent default user directory. The pinned platform is
espressif32 6.13.0 and the expected ESP-IDF version is 5.5.3. The initial build
downloads the RISC-V toolchain, ESP-IDF, CMake and Ninja; a system C compiler
is not needed for firmware builds. Keep `dependencies.lock` unchanged.

First query the existing firmware before flashing. Discovery prioritizes
Espressif USB Serial/JTAG `303a:1001`, then requires the project HELLO identity.
The Windows COM number is assigned by the OS. Inspect it with list_ports;
`--port COM5` is an example manual override, not a required port. CH340 is not
used. Do not use WSL for physical ESP32 access.

On the tested Windows machine, pressing hardware RESET preserves the image
but can leave COM3 enumerated without protocol replies, even with the monitor
closed. Unplug/reconnect USB restores communication. The verified alternative
is an explicit esptool-style USB reset on the known board's port:

```powershell
# Replace COM5 with the port previously identified as this board:
.\.venv\Scripts\python.exe tools/clipctl.py --port COM5 --reset info
```

This resets the board before the usual HELLO identity check; it does not
reflash or erase content. Discovery never resets ports automatically. After
recovery, normal automatic `info`, `list` and upload work again. Firmware
and the vendor driver remain unchanged. The underlying cause of the Windows
hardware-RESET USB behavior is not established; this is a verified workaround.

Only after a successful build, inspect image size, the 4 MB configuration and
the unchanged partition layout; then flash using the discovered port:

```powershell
git status --short
# Set this to the port reported by clipctl info / list_ports:
$clipPort = Read-Host 'ESP32 COM port'
.\.venv\Scripts\python.exe -m platformio run -t upload --upload-port $clipPort
.\.venv\Scripts\python.exe -m platformio device monitor -p $clipPort -b 115200
```

Close the monitor before clipctl. Verify boot, LittleFS mount, active manifest
scene and absence of reset loops. Do not erase Flash or upload a filesystem
image: normal firmware upload must preserve user content.

```powershell
.\.venv\Scripts\python.exe tools/make_asset.py input.png output.bin
.\.venv\Scripts\python.exe tools/clipctl.py upload output.bin --id 2
.\.venv\Scripts\python.exe tools/clipctl.py list
.\.venv\Scripts\python.exe tools/clipctl.py info
```

Choose an unused scene ID (1–65535 on firmware1.7; 1–8 on legacy firmware) to preserve existing scenes. Verify the image
on the LCD, press hardware RESET, then verify the same image and active scene
again. Software ACK alone does not verify the physical display.

## Linux development

Use a compatible Python (3.13 was verified on Ubuntu) and a local environment:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python tools/clipctl.py info
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python -m platformio run
```

Use `--port /dev/ttyACM0` when needed; configure serial-device permissions
according to the Linux distribution. Both platforms use the same Python tools,
wire protocol, assets and firmware sources.

## Native firmware host tests

The existing C tests require a **native** C11 GCC/Clang compiler, separate from
the ESP32 RISC-V toolchain. On Windows use native MinGW-w64 GCC or a compatible
Clang toolchain; no WSL is needed. With the compiler on PATH and after the first
firmware build:

```powershell
.\.venv\Scripts\python.exe tools/run_host_tests.py
```

On Linux use `.venv/bin/python tools/run_host_tests.py`. The runner uses
`tempfile`, discovers cJSON from the permanent PlatformIO installation, and
supports `--cc PATH` and `--cjson DIRECTORY`. It retains both foundation and
modes tests. If no native compiler is installed, these tests cannot run;
Python tests and the firmware build remain separate checks. No large IDE is
required.

## User content over USB

Firmware is configured for **4 MB Flash**. Its [partition layout](docs/storage_layout.md)
has a 2 MB factory app and a 1984 KiB LittleFS partition. The firmware mounts
LittleFS at boot, validates `manifest.json` and the active asset, then displays
it from Flash through the existing 5120-byte LCD strip buffer. Missing or bad
content falls back to the compiled ART/TEXT/ANIMATION demo.

The [asset format](docs/asset_format.md) is static RGB565 v1. The
[USB protocol](docs/usb_protocol.md) is framed protocol v1 with CRC32 and
ACK/NACK. Neither uploading an image nor changing the active scene reflashes
firmware. Firmware1.7 uses a fixed820 KiB library budget, up to205 minimum-block scenes; legacy firmware accepts eight IDs. The initial workflow uses ID1.

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

Historical Ubuntu host-test commands (prefer the portable runner above):

```sh
cc -std=c11 -Wall -Wextra -Werror -Isrc \
  -I/tmp/hairclip-platformio/packages/framework-espidf/components/json/cJSON \
  tests/foundation_test.c src/wire.c src/asset_format.c src/compression.c src/protocol.c \
  src/manifest.c src/upload_guard.c \
  /tmp/hairclip-platformio/packages/framework-espidf/components/json/cJSON/cJSON.c \
  -lm -o /tmp/fntflshkaus6-foundation-test
/tmp/fntflshkaus6-foundation-test
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The LittleFS component is pinned by `src/idf_component.yml` and
`dependencies.lock`. Historical Ubuntu-only workaround: on the old machine,
ESP-IDF's component cache needed a
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

Следующий раздел — исторический workaround старой Ubuntu-машины, не текущий
workflow. Не переносите каталоги `/tmp` на Windows; используйте постоянное
окружение из разделов выше.

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


Firmware1.7 storage policy and safe capacity: [library_capacity.md](docs/library_capacity.md).
