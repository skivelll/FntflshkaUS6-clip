# Animation v1 encoding 1 — firmware 1.8

Compatible optional extension, advertised by capabilities byte7 bit7. Asset header
remains HCAS/version1/type2/RGB565; static and encoding0 bytes are unchanged.
Old firmware rejects encoding1; host must check capability before upload.

16-byte animation prefix: existing count u16, loop=0 u8, encoding=1 u8,
x/y i16, decoded full-frame bytes u32, reserved=0 u32. All integers little-endian.
Index contains count records of28 bytes, struct `<IIIIHHHHBBH>`:
payload-relative offset, stored length, duration_ms, decoded rectangle CRC32;
rectangle x/y/width/height u16; codec u8 (0 raw,1 RLE), flags u8 (1 key), reserved0 u16.
Data is contiguous after the index, with no trailing bytes. CRC covers decoded
rectangle bytes; outer asset CRC covers the complete index and compressed data.

First frame and at least every32nd frame must be a full-size key frame.
Other records replace a rectangle relative to the previous displayed frame.
Unchanged frames use a1×1 rectangle containing the unchanged pixel. No alpha/XOR.
Each key covers the entire asset; delta chains have at most31 frames. Loop always
starts at key0, overwriting all previous pixels inside the asset.

RLE is a sequence of `<HH>` run_count, RGB565_pixel; count1–65535. Decoded pixels
must equal rectangle area exactly; reject under/overflow, zero runs, trailing
bytes, invalid rectangles/flags/offsets/durations/CRCs. Raw length equals area×2.
Firmware decodes directly into the existing DMA strip; LCD RAM retains unchanged
pixels. No full-screen framebuffer, vendor driver or Flash-layout change.

Encoder compares raw/RLE full frame and raw/RLE bounding rectangle, forcing keys
every32 frames. It can retain complete legacy raw encoding when that is smaller.
Optimization is lossless relative to prepared RGB565. Existing820 KiB library/file
budget and A/B upload remain authoritative. Actual optimized bytes, not source
GIF bytes or raw estimate, determine GUI fit and savings.

Encoding1 also bounds count×width×height to32 MP to bound desktop decoded previews.
Source GIF decoding retains selected source frames only, separately capped at32 MP.

Optional command29 BENCH_ANIMATION (bit7), empty request: explicit read-only file
diagnostic; draws at most8 frames of active animation and restarts that same scene,
postponing playlist tick. No content writes. Reply12 u32: schema1,frame_count,
sample_count,validation_us,total_draw_us,max_draw_us,heap_before,heap_after,
minimum_free_heap,largest_before,encoding,decoded_frame_bytes. Times include LCD
DMA completion and flash/decode; they are measured work times, not promised FPS.

## Windows / ESP32-C6 validation, 2026-10-15

Tests use simple generated images with a small moving block; these savings do not
represent full-motion video. Random-noise input selects legacy raw encoding.

| Profile | Frames | Raw bytes | Optimized bytes | Saved | USB upload |
|---|---:|---:|---:|---:|---:|
|128×128|70|2294640|27428|98.80%|4.50 s|
|320×172|40|4403720|17752|99.60%|2.39 s|

On-device command29, eight samples (key0 and seven deltas): compact validation
224536 us, total draw371731 us, maximum161740 us; full-screen validation117007 us,
total draw431039 us, maximum221048 us. A temporary legacy raw noise96×96 animation
also passed: validation48232 us, three draws352856 us, maximum119999 us.
Configured minimum durations170/250/350 ms remain unchanged; compression is not
a promise of higher FPS. Maximum sampled draw time fits the corresponding interval.

Free heap before profiling328388 bytes; after closing diagnostic file333248 bytes;
minimum free262736, largest block311296. No growing firmware frame index, heap
framebuffer or vendor change. Firmware RAM94432 bytes, binary373968 bytes;
Flash4 MB and original partitions unchanged. LittleFS total2031616 bytes; final
free1966080 bytes, library charge49152 bytes, measured after test cleanup.

Both assets uploaded/read back byte-identically. Firmware independently rejected
bad decoded CRC, zero rectangle width, unknown codec, missing initial key, unknown
encoding, zero RLE run and overflowing run, retaining the previous committed file. Interrupted
replacement plus known-board software RESET retained both scenes; boot had no
watchdog/reset loop. Only the two newly created visual test scenes1/2 remain.
Native C11 fixtures/runner now include compression validation, but were not run:
native GCC/Clang is absent. Physical LCD/RESET confirmation is still pending.

Upper-bound test:581 independent full-screen color frames,31978240 decoded pixels,
18632 stored bytes, upload with normal20-second ACK deadline completed in4.906 s.
Validation1114592 us, eight draws1763732 us, maximum223741 us. Known-board RESET:
USB ready3216 ms, full readback exact, no watchdog; temporary fixture removed.
An earlier validator expanded every RLE pixel for CRC and exceeded the ACK deadline.
The final validator combines CRCs of repeated pixels with reflected IEEE polynomial
operators, checks decoded bounds without allocating expanded rectangles, and buffers
encoded reads in512 bytes. Playback still decodes into the original DMA strip.
ROM CRC is used for ordinary decoded blocks; protocol CRC implementation is unchanged.
Yield checks are time-based, so highly compressed data cannot introduce thousands
of artificial scheduler delays. Watchdog remains enabled.

Generated operators occupy2 KiB of read-only data, not runtime frame memory.
tools/generate_crc_operators.py regenerates them; Python tests compare random/power
boundaries against independent zlib CRC32. The polynomial concatenation identity is
also documented by [zlib's CRC implementation](https://raw.githubusercontent.com/madler/zlib/v1.2.11/crc32.c).
123 desktop +9 tools tests passed, including stale/actual-size GUI, byte-exact codec,
malformed records, raw fallback, archive/edit/upload guards and cache budgets.

The GIF dialog performs one bounded background analysis job at a time, ignores
stale option results, and uses the same RGB565 preparation/encoder/placement as
upload. It displays actual file size, raw comparison, savings, rounded library
charge and safe staging-space checks. Analysis does not read or mutate the device.
Size-only analysis continues past820 KiB without creating an uploadable oversized
asset. It computes the full optimized size and exact prefix sizes through the same
raw/RLE/delta choice logic. On overflow, the dialog reports file/library excess and
the longest fitting prefix, with the corresponding source last-frame number.
This suggestion removes the tail only; changing step/profile requires recalculation.
No frames are truncated automatically, and ordinary encoding/upload limits remain.
Expanded animation previews share a32 MP RAM budget; older expanded previews can
be evicted and rebuilt from the verified compressed RAM assets without USB reads.
The full verified asset cache remains until app close/device identity change.
