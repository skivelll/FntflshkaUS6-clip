# Scene collection phase — 2026-10-15

Six requested features are implemented: device-side titles, persisted order,
safe image replacement, thumbnails/active marker, hardware persistence checks,
and eager application-lifetime scene caching. No GIF/fonts/slideshow work added.

## Implementation

- Firmware 1.2 adds GET_SCENE_DETAILS (12), RENAME_SCENE (13), REORDER_SCENES
  (14), advertised by capabilities byte 7 bit 1. READ_SCENE remains bit 0.
  Protocol and asset versions remain 1; LIST_SCENES and all existing command
  layouts remain unchanged. Old host CLI still works.
- Manifest version remains 1. Optional `title` defaults to empty for legacy
  scenes. Titles support up to 48 UTF-8 bytes, validated against invalid UTF-8
  and ASCII controls. Quotes/backslashes are JSON-escaped using cJSON. The
  parser/encoder capacity is 4096 bytes. Rename/order use the existing atomic
  manifest commit. See usb_protocol.md for downgrade-size compatibility limits.
- Reordering submits a complete ID/checksum permutation and preserves active
  ID. It organizes the list; it does not switch pictures automatically.
- Replacement is explicit and confirmed, checks the old checksum before BEGIN,
  retains ID/title/order and commits through the existing A/B file mechanism.
  New uploads still choose a free ID. Firmware upload safety is retained.
- SceneCache preloads all current assets on connection in the existing serial
  worker. Progress is shown, conflicting controls wait for completion and Qt
  remains responsive. Selection only uses RAM: it does not request READ_SCENE.
- Cache keys include content identity (ID/checksum/dimensions); different USB
  identity clears the cache. Metadata/order/active changes do not reload pixels.
  Replacements/additions seed the cache from the acknowledged uploaded bytes;
  deleted records are pruned. Reconnect to the same device reuses verified assets.
  Cache is not written to disk and lives until app close.
- Thumbnails are generated from exact RGB565 pixels and cached. Sidebar selection
  and scroll position survive polling. User-facing controls remain Russian.
- Serial reads now wait for a byte and consume available bursts instead of
  waiting for a fixed 256-byte buffer to fill at every short ACK/final fragment.
  Noise/CRC/sequence validation is unchanged and covered by fragmented tests.

## Real Windows/ESP32 verification

- Native Windows 11 / Python 3.12.10; existing PySide6/Pillow/pyserial versions
  unchanged. COM3 discovered and HELLO verified; ESP-IDF 5.5.3 and platform
  espressif32 6.13.0 retained.
- Build and flash SUCCESS: firmware.bin **356112 bytes**, +4272 from 1.1.
  RAM **13072 bytes** (+416 for title fields). Increase is expected from
  metadata handling/JSON serialization; no dependency upgrade or full framebuffer.
- Generated partition table matches the original 4 MB configuration; vendor
  driver, partitions.csv, sdkconfig.defaults and dependencies.lock unchanged.
- Before flash: user scenes 1–7, scene 6 ACTIVE, total/free 2031616/1204224.
  After flash IDs/checksums/active state remained identical; legacy unnamed
  manifest read correctly.
- Real GUI preloaded seven assets in **46.188 seconds**, **1490 READ_SCENE
  requests**. Selecting every scene three times (21 selections) took **1.562
  seconds total** and sent **zero additional READ_SCENE requests**.
- Created only a temporary fixture at ID 8. GUI renamed it to `Тест "кэш" ★`,
  moved it to the first position and replaced its 2×2 asset with a 320×172
  110104-byte image. Same ID/name/order retained; A/B switched to 0008b.bin.
  Pixel CRC after replacement: **ae10c0de**. No asset rereads during metadata
  changes or replacement; upload bytes seeded the verified cache.
- User confirmed the blue/white test image on LCD and physically pressed RESET.
  Image persisted. Known Windows USB recovery ran only on the already trusted
  serial identity; fresh snapshots verified title/order/checksum/file/ACTIVE
  persisted. READ_SCENE count stayed at 1490, so recovery did not reload scenes.
- Restored original user order and active scene 6, then deleted only fixture 8.
  All user scenes 1–7 and their checksums remain. Free space returned from
  1093632 to **1204224 bytes** (total **2031616**). App left open with the cache.
- A one-time test script hit Windows cp1251 console encoding when printing ★;
  diagnostics were changed to ASCII-safe JSON. The runtime app uses Unicode Qt
  and UTF-8 log files; persisted title and real device data were verified intact.

## Tests and review

42 desktop tests + 8 existing CLI tests PASS; pip check, syntax and diff checks
PASS. New tests cover eager cache retention, no reads after metadata changes,
USB identity changes, stale replacement checks, interrupted replacement safety,
Unicode title limits, reorder, full Qt workflow and serial fragmentation/CRC/
sequence handling. The old GUI test was updated to wait for preload readiness
instead of merely waiting for the first snapshot; startup disables conflicting
controls immediately and enables offline import if discovery finds no device.

C foundation tests were expanded for title JSON escaping, invalid UTF-8/control
characters, reorder permutations and legacy manifests without title. They remain
unexecuted here because a native host C11 compiler is absent; this limitation
is not reported as a pass. Firmware build plus real metadata/RESET checks passed.

Changed firmware: manifest.h/.c, storage.h/.c, protocol.h, usb_service.c.
Changed desktop: client/models/discovery/worker/mock, core protocol/transport,
scenes models and main_window. New device/cache.py and collection/transport tests.
Updated README, protocol/storage docs and AGENTS.md. Previous uncommitted
migration/MVP changes preserved. No commit or push.

Ignored local evidence: .pio/collection/result.json, ready-for-reset.json,
screenshots, test PNG and GUI harness. Runtime logs remain in user-data storage.
