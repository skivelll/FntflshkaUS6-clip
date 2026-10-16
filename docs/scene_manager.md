# Scene manager and device previews — 2026-10-15

Firmware1.7: актуальные лимиты, pagination, quota и cleanup описаны в
[library_capacity.md](library_capacity.md). Ограничения восьми сцен/512 KiB
в описаниях предыдущих версий ниже относятся к legacy firmware.

## Scope

Desktop now supports selecting a stored scene, activating it, deleting with
confirmation, refreshing storage and previewing the actual asset read from
LittleFS. The user confirmed that switching scenes changes the physical LCD.
The user also confirmed that the preview after firmware 1.1 matches the LCD
and that the updated workflow works correctly.

Rename/reorder on the device remain outside this implementation: protocol v1
does not define commands for those changes. No local-only names or order are
presented as device metadata.

## Compatible READ_SCENE extension (authorized by the user)

The user explicitly requested reading existing images from the board rather
than a local cache. Firmware **1.1** adds command **11 READ_SCENE** and advertises
it in capabilities byte 7 bit 0 (previously reserved). Framing/protocol version
remain **1**; asset format remains **1**; existing commands and response record
sizes are unchanged. See usb_protocol.md for wire details and error conditions.

Firmware reads bounded chunks, checks manifest identity/checksum and range,
rejects reads during an upload and validates the asset on its first chunk.
The desktop checks offsets, complete length, header and payload CRC and manifest
dimensions/checksum before displaying. Reads do not activate or modify scenes.
No full-screen framebuffer is introduced on the ESP32.

Serial operations remain on the single Qt worker. Preview reads show progress,
can be cancelled when another scene is selected, ignore stale results and use
an in-memory cache of at most eight assets. Small stored images are centered on
the actual display dimensions with black margins, matching the firmware.
Old firmware continues to support management; it receives no READ_SCENE request
when its capability flag is absent and shows an unavailable-preview message.

## Verification

- **34 desktop tests + 8 CLI tests PASS**, pip check and git diff --check PASS.
- Tests cover ACK verification, stale selection, confirmation cancellation,
  active/inactive deletion semantics, selection retention after polling,
  read CRC/ranges/cancellation, old firmware behavior, centered device preview
  and ignoring a result from a previously selected scene.
- Build SUCCESS: ESP-IDF 5.5.3, espressif32 6.13.0, Flash still 4 MB.
- `firmware.bin` **351840 bytes**, +512 from 351328. RAM remains 12656 bytes.
- Generated partitions.bin decoded and matched the original layout. Vendor
  LCD driver, partition table source, sdkconfig.defaults and dependencies.lock
  unchanged. No filesystem image upload or erase of LittleFS.
- Before flash: eight scene records including the test fixture; LittleFS
  total/free **2031616/1204224**, IDs/checksums/active flags saved locally.
- Flash to discovered COM3 SUCCESS, written hashes verified. After flash,
  firmware 1.1, all eight records/checksums/active flags and free space matched.
- All eight assets read from real LittleFS; every complete asset CRC verified.
  Scene 1: 99096 bytes; scenes 2–7: 110104 bytes; test fixture 8: 32 bytes.
- Real GUI selection of scene 7 fetched the same complete bytes as the verified
  read artifact and displayed a 320×172 preview. During the read the Qt heartbeat
  fired 877 times. Screenshot inspected; GUI remained responsive. App left open
  for the user's final LCD comparison; the user explicitly confirmed success.
- Real invalid reads (bad payload size, zero length, wrong manifest checksum,
  offset at EOF) returned expected NACK codes.
- Only the 2×2 fixture created for this test (ID 8, CRC 63666ca7) was deleted.
  User scenes **1–7** retained their checksums. Scene **7 ACTIVE** restored;
  total/free remains **2031616/1204224**. Delete and activation ACKs verified
  with LIST_SCENES and storage. No user scene was deleted.
- Initial one-time GUI automation hit a callback-order issue; activation was
  then exercised manually by the user and confirmed on LCD. The temporary
  fixture was cleaned up through the same verified DeviceClient API. The
  callback ordering in the ignored test harness was corrected separately.
- Native C host tests remain unexecuted: no native host C11 compiler installed.
  Firmware build and real-device read/error tests are the firmware validation.

## Files and limitations

Changed firmware: src/protocol.h, storage.h/.c, usb_service.c. Changed desktop:
core/protocol.py, device/models.py/client.py/worker.py/mock.py,
ui/main_window.py/preview.py. New tests: test_scene_manager.py, test_scene_read.py.
Updated protocol docs and desktop README. Previous migration/MVP changes remain
uncommitted and preserved; no commit or push.

First preview reads can take tens of seconds with the existing serial transport;
progress is visible and subsequent cached selection is instant. Cached scenes
are invalidated by their port/ID/checksum/dimensions identity. No scene file
editing, rename/reorder, GIF, fonts or new asset format is added.

Ignored local artifacts: .pio/scene-baseline.json and .pio/scene-manager/ assets,
read-verification.json, screenshots and one-time verification scripts.
