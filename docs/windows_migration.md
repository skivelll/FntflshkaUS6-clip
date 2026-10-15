# Windows migration verification

Status: the native Windows build/flash/content/display/persistence chain is
verified, with a documented USB recovery workaround after hardware RESET.
Unassisted USB reconnection after hardware RESET is not at full parity.
No GUI work is part of this migration.

## Environment observed on 2026-10-13

- Windows 11 Pro, version 10.0.26200.
- Git 2.55.0.windows.5.
- Existing ignored repo-local `.venv`: Python 3.12.10, PlatformIO Core 6.2.0,
  pyserial 3.5, Pillow 12.3.0. No additional Python installation was needed.
- Python 3.14 is also installed, but was not used.
- Platform: espressif32 6.13.0; build confirmed ESP-IDF 5.5.3
  (framework-espidf 3.50503.0), CMake 3.30.2, Ninja 1.9.0 and
  toolchain-riscv32-esp 14.2.0+20251107. Permanent default PlatformIO storage.
- USB serial COM3, VID:PID 303a:1001, serial AC:EB:E6:3E:94:90. Windows
  identifies the serial interface as USB Serial Device; the composite device
  also exposes USB JTAG/serial debug unit. No CH340 driver was installed.

## Existing firmware, before any flashing

Automatic discovery and HELLO identity validation succeeded. `clipctl info`:

```text
port=COM3 firmware=FntflshkaUS6 clip 1.0 protocol=1 asset=1
display=320x172 pixel_format=1 flash_configured=4194304 physical_flash=unknown
storage_total=2031616 storage_free=1871872 max_asset=110104 max_chunk=512 render_buffer=5120
```

`clipctl list`:

```text
id=1 order=0 type=1 288x172 crc=fbfac7aa ACTIVE scenes/0001a.bin
```

This is the observed Windows baseline; older Ubuntu free-space values describe
a different content state. Windows flashing subsequently confirmed ESP32-C6FH8
revision v0.2 with embedded 8 MB Flash. Configuration remains 4 MB.

## Checks and changes

- Python unittest: 8 tests PASS, including discovery priority and rejection of
  a different firmware identity; pip check PASS.
- Existing C foundation/modes suites retained. Native host compiler absent;
  `tools/run_host_tests.py` reports this prerequisite explicitly. These C
  suites have not yet run on this machine.
- Removed fixed Linux upload/monitor ports from platformio.ini.
- Python tools retain pathlib and cross-platform serial discovery; one port
  enumeration snapshot replaces two potentially inconsistent enumerations.
- Added minimal pinned requirements and permanent project instructions.
- README separates native Windows/Linux setup and labels old /tmp commands
  as Ubuntu-only historical workarounds.
- Firmware sources, vendor driver, protocol, asset format, lockfile and
  partition table have not been modified.

## Partition layout (unchanged)

| Partition | Offset | Size |
| --- | --- | --- |
| NVS | 0x9000 | 0x6000 (24 KiB) |
| PHY | 0xF000 | 0x1000 (4 KiB) |
| Factory app | 0x10000 | 0x200000 (2 MiB) |
| LittleFS | 0x210000 | 0x1F0000 (1984 KiB) |

## Native build and flash

- PlatformIO build SUCCESS on Python 3.12.10 / ESP-IDF 5.5.3.
- `firmware.bin`: 351328 bytes, 32 bytes smaller than Ubuntu's ~351360.
  PlatformIO reports linked Flash usage 350928 bytes and RAM 12656 bytes.
- Decoded generated `partitions.bin` matches all four original partition
  labels, offsets and sizes. Firmware image header specifies 4 MB Flash.
- git status checked before upload. Native `platformio run -t upload
  --upload-port COM3`: SUCCESS; written data hashes verified.
- Erased/written ranges were bootloader 0x0–0x5fff, partition table
  0x8000–0x8fff and firmware 0x10000–0x65fff. LittleFS was not erased.
- After flashing, HELLO/info/list succeeded: original scene 1 checksum
  fbfac7aa remained active, LittleFS total/free remained 2031616/1871872.
- The first serial capture was opened after boot and contained no output;
  full boot logging still requires a RESET while capture is open.
- Local test PNG generated: 320x172, white border, three color blocks and
  `WINDOWS USB TEST - SCENE 2`. make_asset produced valid 110104-byte asset v1
  in ignored `.pio/migration/`.
- Upload to previously unused scene 2 completed: 110104/110104 bytes,
  UPLOAD_END ACK, LIST_SCENES confirmed `scenes/0002a.bin`, CRC 82c63826,
  active 320x172. Scene 1 remains listed with its original checksum.
- After content upload, LittleFS total/free: 2031616/1761280 bytes.

## Hardware RESET and USB recovery

- User confirmed that the test scene displays correctly and the same image
  returns after physical hardware RESET.
- During RESET, the open Windows serial handle failed with ClearCommError
  (device no longer functioning). No boot log was captured.
- After RESET, COM3 remains enumerated with the same identity, but repeated
  HELLO/list/storage/ping attempts timed out. A read-only probe with DTR false
  and true also timed out. Thus post-RESET USB parity is not yet confirmed.
- User was asked to unplug/reconnect USB to distinguish re-enumeration from
  firmware protocol availability.
- After user unplugged/reconnected USB: automatic HELLO/info/list/PING all
  succeeded; scene 2 remains active with CRC 82c63826, total/free unchanged
  at 2031616/1761280. Cold-start persistence is confirmed by protocol.
- Second physical RESET with COM port closed reproduced the missing HELLO
  replies. The failure is not specific to an open monitor.
- An addressed `pnputil /restart-device` attempt failed with Access is denied.
  No driver changes or privileged device restart were performed.
- Standard esptool 4.11.0 USB hard-reset line sequence (RTS true, wait 0.2 s,
  RTS false, wait 0.2 s; DTR written after each RTS for usbser.sys) restored
  communication without unplugging or reflashing.
- Added **explicit** `clipctl.py --port PORT --reset info` recovery. It requires
  a manually specified known board port and resets before the normal identity
  handshake. Automatic discovery never resets arbitrary devices. Recovery
  was verified on COM3; subsequent automatic list/PING also succeeded.
- Boot log after USB reset confirms ESP-IDF 5.5.3, 4 MB Flash, original partition
  layout, LittleFS total/free 2031616/1761280, active scene 2 size 320x172 and
  USB protocol v1 ready. No reset loop observed during capture and follow-up
  requests. Log saved locally in ignored `.pio/migration/software-reset-boot.log`.

```text
I (483) STORAGE: LittleFS total=2031616 free=1761280
I (1033) USB: active scene=2 size=320x172
I (1033) USB: FntflshkaUS6 clip USB protocol v1 ready
```

## Limitations

- Hardware RESET preserves and displays the asset, but Windows USB communication
  requires explicit recovery or a cable reconnect. The root cause is not proven;
  do not claim this is fixed in firmware or fully automatic RESET parity.
- Native C host suites remain unexecuted because no native C11 compiler is
  installed. Runner and instructions are provided; tests were not removed.
- Linux execution was not repeated on this Windows machine. No deliberate
  Linux incompatibility was introduced; shared formats/firmware remain unchanged.

## Changed files

AGENTS.md, README.md, platformio.ini, requirements.txt, requirements-dev.txt,
tools/clipctl.py, tools/run_host_tests.py, tests/test_tools.py,
docs/windows_migration.md. Test PNG/assets/probes/build products are ignored
under .pio. No src firmware files changed.

See README.md for clean Windows setup commands. No commits or pushes made.
