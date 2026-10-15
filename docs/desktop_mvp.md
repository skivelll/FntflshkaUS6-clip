# Phase 2 — Desktop MVP verification

Verified natively on Windows 11 Pro on 2026-10-13, Python 3.12.10.
The user confirmed the uploaded GUI image appears on the physical LCD.
Phase 2's static-image GUI workflow is complete; no firmware flashing or
hardware RESET was performed in this phase.

## Milestones

- A: editable desktop package installed, PySide6 6.10.3 native window launched,
  theme and landscape preview created, protocol-level mock tested.
- B: actual Qt DeviceWorker automatically discovered COM3, validated HELLO
  identity and received GET_CAPABILITIES/storage/LIST_SCENES.
- C: PNG/JPEG/WEBP import, FIT/FILL, scale 10–400%, X/Y, selected background,
  alpha, nearest-neighbor option, RGB565 preview and exact asset size verified.
- D: native Qt end-to-end harness invoked the actual add-image button handler
  with a selected PNG and clicked the actual upload button with QTest.
  Upload ran in the real serial worker, not CLI or mock; the user compared
  the GUI preview to the real LCD and confirmed the image appeared correctly.

## Real device

| Field | Result |
| --- | --- |
| Port | COM3 (discovered, never hardcoded in application) |
| USB identity | 303a:1001, serial AC:EB:E6:3E:94:90 |
| Firmware | FntflshkaUS6 clip 1.0 |
| Protocol / asset | 1 / 1 |
| Display / pixel format | 320×172 / RGB565 (1) |
| Configured Flash | 4194304 bytes (4 MB) |
| Physical Flash via capabilities | 0 = unavailable; historical esptool 8 MB does not change configuration |
| LittleFS total | 2031616 bytes |
| Maximum asset | 110104 bytes |
| Maximum chunk | 512 bytes |
| Render buffer | 5120 bytes |

## GUI upload result

- Source PNG: generated geometric test image, 640×344; pink border, three
  colored blocks, text `DESKTOP MVP / GUI UPLOAD / :3`.
- Prepared display/asset: 320×172, FIT at 100%, RGB565-preview decoded from
  the exact payload being sent. Asset v1 size: **110104 bytes**.
- First free ID chosen from LIST_SCENES: **3**, not a fixed application ID.
- UPLOAD_BEGIN/chunks/UPLOAD_END completed; ACK verified.
- LIST_SCENES after upload: scene 1 CRC fbfac7aa, scene 2 CRC 82c63826 retained;
  new **scene 3 ACTIVE**, file `scenes/0003a.bin`, payload CRC **bd7f3fa7**.
- Storage free before/after: **1761280 → 1650688 bytes**. Actual LittleFS
  consumption 110592 bytes; file size remains exactly 110104 bytes.
- Upload took approximately 48 seconds with the existing serial transport.
  Qt heartbeat fired **777 times during upload**, confirming the UI remained
  responsive while the worker waited for USB replies.
- User explicitly confirmed correct image on physical LCD. RESET not requested.

Local verification artifacts (ignored, not committed):
`.pio/desktop-e2e/result.json`, source PNG, output.bin, expected-lcd.png,
GUI screenshots and the one-time Qt E2E harness. Runtime logs are outside the
repository in the application's Windows user-data directory.

## Tests

21 desktop tests and 8 existing CLI/tool tests PASS; pip check PASS.
Desktop tests cover color encoding/roundtrip, FIT/FILL/crop offsets, alpha,
nearest-neighbor, exact size, smaller display capabilities, storage reserve,
mock ACK/NO_SPACE/disconnect, full scene slots, identity checks, trusted recovery
and changed serial-number rejection. Qt integration exercises buttons,
rendering, mock upload, progress, ACTIVE and GUI responsiveness.
Hardware tests are not part of the automatic unit suite.

Firmware C host tests remain as documented in Windows migration: native
C11 compiler unavailable. Firmware sources, dependencies.lock, partitions.csv,
sdkconfig.defaults and vendor driver are unchanged by Desktop MVP.

## Architecture and changed files

New `desktop/pyproject.toml`, desktop/README.md and
`desktop/src/fntflshka_us6_clip/` package:

- main/__main__: launch, Qt application and rotating user-data logger;
- core: shared asset, USB framing/identity, serial transport and reset sequence;
- device: typed models, injectable client, safe discovery/recovery, one Qt
  serial worker and mock backend;
- media: static image loading, placement, RGB565 roundtrip and image worker;
- scenes: wire records, free ID and storage estimate;
- ui: main window, aspect-preserving preview, placement controls;
- resources/theme.qss: Russian UI's restrained purple/pink theme;
- desktop/tests: device, media, shell and Qt workflow tests.

Updated CLI tools import shared modules and remain directly executable.
README.md links the Windows desktop workflow. AGENTS.md records the lasting
desktop rules. .gitignore excludes editable metadata/build outputs.
Existing uncommitted migration changes were preserved. No commit or push.

## MVP limitations

- Static PNG/JPEG/WEBP only, up to 40 MP; animated files are rejected.
- Eight scenes, append to a free slot; no GUI rename/delete/reorder or stored
  image download. Existing device scenes are listed, not editable previews.
- The first connection after an unresponsive hardware RESET cannot safely
  recover an unknown device: unplug/reconnect USB. In-session recovery requires
  prior HELLO/capabilities verification and the same port/VID/PID/USB serial.
- No automatic retry after interrupted upload. The firmware preserves the
  previous active scene; reconnect USB to clear an unfinished upload if needed.
- Estimated remaining memory subtracts exact asset bytes; actual filesystem
  block/metadata usage is refreshed from GET_STORAGE after upload.
- RGB565 preview reproduces file colors, not physical LCD/backlight calibration.
- No installer/exe bundling, GIF, fonts, text scenes or wireless features.

See [desktop/README.md](../desktop/README.md) for setup and launch commands.
