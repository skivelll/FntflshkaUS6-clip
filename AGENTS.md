# FntflshkaUS6 clip

## Hardware and firmware

- Hardware: Waveshare ESP32-C6-LCD-1.47, ESP32-C6 with built-in Espressif USB Serial/JTAG (VID:PID 303a:1001), not CH340.
- LCD is physically 172x320; the verified logical orientation is landscape 320x172. Preserve rotation, centering, integer scaling and strip/DMA rendering with a 5120-byte buffer; no full-screen framebuffer.
- Framework: ESP-IDF through PlatformIO. Working ESP-IDF version: 5.5.3; platform espressif32 is pinned to 6.13.0. Preserve dependencies.lock and the LittleFS component version. Do not upgrade dependencies without a concrete reason.
- The vendor Vernon_ST7789T LCD driver is hardware-tested. Do not rewrite it without necessity. Preserve working firmware rather than refactoring for architecture alone.
- Intentionally configure Flash as 4 MB. esptool previously reported physical 8 MB; switching to 8 MB requires a separate explicit decision.
- Partition layout: NVS 0x9000/0x6000; PHY 0xF000/0x1000; factory app 0x10000/0x200000; LittleFS storage 0x210000/0x1F0000. Preserve existing data; do not erase/format LittleFS unnecessarily.

## Content and compatibility

- USB protocol v1: docs/usb_protocol.md; asset format v1 (static RGB565): docs/asset_format.md; storage: docs/storage_layout.md.
- User content updates over USB without reflashing firmware. Preserve framing, sequence IDs, CRC32, ACK/NACK, asset validation and backward compatibility; do not introduce incompatible protocol changes without a concrete reason.
- Uploads use temporary files, inactive A/B slots and manifest commit. Interrupted/invalid uploads must preserve the previous working scene. Valid content must persist across hardware RESET.
- tools/make_asset.py converts images; tools/clipctl.py discovers ports using serial.tools.list_ports, prioritizes 303a:1001, and requires HELLO identity verification. VID/PID alone is insufficient. Keep explicit --port support.
- The future desktop app is named FntflshkaUS6 clip and targets Windows. Keep firmware and desktop maximally separate. Do not start GUI work unless requested.
- Do not add Wi-Fi or Bluetooth without a separate task.

## Development

- Work natively on Windows 10/11. Never use WSL for physical ESP32 access. Retain Linux compatibility in Python tools and documentation.
- Use repo-local .venv (ignored by git), python -m pip and python -m platformio. Avoid Python 3.14 until compatibility is verified; Python 3.13 was used on Ubuntu, and Python 3.12.10 successfully builds ESP-IDF 5.5.3 on Windows.
- Use permanent PlatformIO storage; /tmp toolchain/cache paths in old Ubuntu instructions were machine-specific historical workarounds.
- Use pathlib, tempfile and sys.executable where appropriate. Do not hardcode usernames, drive letters or COM numbers. Close serial monitors before clipctl or flashing.
- On Windows, if hardware RESET leaves an enumerated USB port unresponsive, the verified recovery is clipctl.py --port PORT --reset info or unplug/reconnect USB. Explicit reset is only for a known board port; never reset arbitrary ports during discovery. It does not erase user content. Do not assume USB reconnect parity until independently verified.
- Check git status before large changes and before flashing. Inspect git diff at the end of each stage. Do not commit or push unless explicitly requested.
- Run tests after changes: Python unittest discovery in tests; retain foundation_test.c and modes_test.c and run host tests with a native C11 compiler when available. Document any unexecuted checks.
- Before flashing, first try clipctl info/list against installed firmware; build and verify Flash/partition configuration and image size. Firmware build does not prove LCD output or hardware RESET persistence: obtain physical confirmation.

## Desktop MVP

- Native Python 3.12/PySide6 package lives in desktop/src/fntflshka_us6_clip; desktop/pyproject.toml separates desktop dependencies from firmware tooling. Run with python -m fntflshka_us6_clip after editable installation, or use the installed GUI entry point. See desktop/README.md.
- core modules are Qt-independent and shared by CLI and GUI. Do not duplicate framing, asset codec or upload validation. Preserve direct tools/clipctl.py and tools/make_asset.py execution.
- UI is Russian. Serial/discovery/upload run in one dedicated Qt worker thread; image preparation runs in another. Never block the Qt event loop. Preview must decode the exact outgoing RGB565 payload and use actual display capabilities within asset v1 limits.
- Recovery may reset only a previously identified device with the same port/VID/PID/nonempty serial number in the current session; never reset unknown ports based on VID/PID alone. Do not automatically retry interrupted uploads.
- New GUI uploads use a free ID 1–8 and preserve existing scenes. Explicit confirmed replacement may update a selected existing ID, checking its previous checksum and retaining its title/order through A/B commit. Confirm ACK, registered checksum/dimensions, ACTIVE and refreshed storage. Never claim LCD verification without physical confirmation.
- Run both Python suites after desktop changes: unittest discover -s desktop/tests -v and unittest discover -s tests -v. --mock must never access real serial hardware. Runtime logs belong in the user-data directory, not the repository.
- Firmware 1.1 adds optional READ_SCENE (command 11), advertised by capabilities byte 7 bit 0, as an authorized compatible protocol v1 extension. Asset v1, Flash layout and existing commands remain unchanged. Check the flag before reading; verify complete asset CRC and manifest identity before preview. See docs/usb_protocol.md and docs/scene_manager.md.
- Scene deletion requires GUI confirmation (cancel by default); verify ACK, refreshed LIST_SCENES and storage. Deleting the active scene selects firmware fallback. Keep a selected scene stable during polling and reject stale ID/checksum actions.
- Firmware 1.2 adds optional GET_SCENE_DETAILS/RENAME_SCENE/REORDER_SCENES (12–14), advertised by capabilities byte 7 bit 1. Original LIST_SCENES is unchanged. Manifest v1 accepts optional UTF-8 title (max 48 bytes) and up to 4096 bytes; legacy records remain readable. Rename and order use the existing atomic manifest commit. Order is not a slideshow.
- Preload all device assets once on connection in the serial worker with progress; retain the verified cache in RAM until app close. Selecting a cached scene must never fetch its asset. Metadata/active changes do not invalidate pixels; replacement/addition updates only changed assets, deletion prunes removed records. Invalidate on a different USB identity. See docs/scene_collection.md.
- Windows portable packaging uses desktop/packaging/clip.spec and tools/build_desktop.py; build-only dependencies are pinned separately in desktop/requirements-build.txt. Keep the complete onedir folder, including _internal. Do not bundle PlatformIO/toolchains or change firmware for packaging. See docs/desktop_release.md.
- Keep local preferences and the single-instance lock in the user-data directory. Persist only window/image settings, never USB trust or asset cache. Demo preferences/lock are separate from real hardware.
- tools/verify_desktop.py checks the extracted ZIP with a sanitized app environment. Bundled --smoke-test requires --mock and never accesses USB; --device-check is a read-only real-device/cache/reconnect diagnostic and must not mutate scenes. It does not establish physical unplug/RESET/LCD verification.
- Interactive preview translates mouse drags into LCD pixels; slider/numeric scale/wheel share one placement state. Preserve exact outgoing RGB565 preview and perform conversions in the image worker. Scene editing uses immutable verified cached RGB565; original PNG/cropped content cannot be recovered. Save only through explicit confirmed same-ID A/B replacement with USB identity and checksum validation; cancel never writes. See docs/image_editor.md.
- Storage forecasts are logical-file estimates: protocol v1 does not expose old A/B slot size or LittleFS allocated blocks. Never promise exact deletion recovery or subtract the old file before replacement. Show actual free-space change after ACK. Future phases are recorded in docs/roadmap.md.
- Continuous preview gestures must show intermediate frames from the current source without timer starvation. Keep one bounded render job, allow upload only of the latest completed state, and use source revisions to reject old-source frames. Native Pillow RGB565 channel operations must remain byte-identical to the scalar codec; retain exhaustive color/parity tests.
- Explicitly exported/imported .fclip archives live in a separate user-data library; they are not preferences, USB trust or the automatic RAM cache. Desktop archive v1 wraps unchanged asset v1. Validate bounded ZIP/JSON, strict paths/entries, asset CRC and SHA256; never extract untrusted ZIP paths. File IO/offline preview preparation runs in LibraryWorker. See docs/library.md.
- Collection restore requires a reviewable per-scene ID plan (add/explicit replacement/skip), defaults to no overwrites, preserves unrelated scenes and never deletes automatically. Save a complete pre-restore backup before the first write; failure blocks restore. Recheck snapshot/identity/checksums/space, seed cache from committed uploads, verify final pixels/metadata/ACTIVE/storage. Batch restore is not atomic: report acknowledged IDs and uncertain steps, allow cancellation, never retry interrupted writes automatically.
