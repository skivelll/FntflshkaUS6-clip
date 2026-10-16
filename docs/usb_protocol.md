# FntflshkaUS6 clip USB Serial protocol v1

Firmware1.7: актуальные лимиты, pagination, quota и cleanup описаны в
[library_capacity.md](library_capacity.md). Ограничения восьми сцен/512 KiB
в описаниях предыдущих версий ниже относятся к legacy firmware.

Transport: USB Serial/JTAG CDC at 115200, 8N1. PlatformIO/esptool flashing
remains available on the same port. The CLI scans for USB ID `303a:1001`, then
requires a valid `HELLO` response containing `FntflshkaUS6 clip`.
The `HELLO` identity changed with the project rename. Older host CLIs that
expect the former name must be updated. Frame and asset magic values remain
unchanged so scenes already stored in LittleFS stay readable.

## Frame

All multibyte fields are little-endian. Maximum payload: 520 bytes.

| Offset | Size | Field |
| --- | ---: | --- |
| 0 | 4 | ASCII magic `HCUP` |
| 4 | 1 | protocol version `1` |
| 5 | 1 | command |
| 6 | 2 | sequence ID |
| 8 | 2 | payload length |
| 10 | N | payload |
| 10+N | 4 | IEEE CRC32 over bytes 0 through 9+N |

Response command `0x80` is ACK; `0x81` is NACK. Both echo the request's
sequence ID. ACK payload starts with the request command, followed by result
data. NACK payload is `[request command, error code]`. The host validates
magic, version, length, sequence and CRC. Other serial log text is ignored.

| Request | ID | Request payload | ACK data |
| --- | ---: | --- | --- |
| HELLO | 1 | empty | ASCII `FntflshkaUS6 clip` (17 bytes), firmware major, minor |
| GET_CAPABILITIES | 2 | empty | 32-byte capabilities record below |
| GET_STORAGE | 3 | empty | total u32, free u32 |
| LIST_SCENES | 4 | empty | count u8, then ordered scene records |
| UPLOAD_BEGIN | 5 | scene ID u32, file size u32, whole-file CRC32 u32 | empty |
| UPLOAD_CHUNK | 6 | offset u32, 1–512 bytes | empty |
| UPLOAD_END | 7 | empty | empty |
| DELETE_SCENE | 8 | scene ID u32 | empty |
| SET_ACTIVE_SCENE | 9 | scene ID u32 | empty |
| PING | 10 | up to 519 bytes | identical bytes |
| READ_SCENE (optional, firmware 1.1+) | 11 | scene ID u32, pixel CRC32 u32, file offset u32, requested length u16 (1–512) | echoed offset u32, up to requested length bytes from asset v1 |
| GET_SCENE_DETAILS (optional, firmware 1.2+) | 12 | scene ID u32, pixel CRC32 u32 | title UTF-8 byte length u8, title bytes |
| RENAME_SCENE (optional, firmware 1.2+) | 13 | scene ID u32, pixel CRC32 u32, title UTF-8 bytes (0–48) | empty |
| REORDER_SCENES (optional, firmware 1.2+) | 14 | count u8, then complete ordered list of (ID u32, pixel CRC32 u32) | empty |

Capabilities record: protocol version u8, asset version u8, display width u16,
height u16, pixel format u8, capability flags u8 (formerly reserved), configured Flash bytes u32,
physical Flash bytes u32 (0 if unavailable), LittleFS total bytes u32,
LittleFS free bytes u32, maximum asset file bytes u32, maximum upload chunk
u16, render buffer bytes u16. Applications must use LittleFS free space rather
than physical Flash size to decide whether content fits.

Capability flag bit 0 advertises READ_SCENE; bit 1 advertises scene metadata
(GET_SCENE_DETAILS/RENAME_SCENE/REORDER_SCENES); bit 2 advertises autonomous
playlist GET_PLAYLIST/SET_PLAYLIST (firmware 1.3); bit3 transition modes (1.4),
bit4 active-scene diagnostics (1.5), bit5 RGB565 animations (1.6). Bits6–7 are reserved.
Firmware 1.0 returns zero here and does not implement command 11. This is an
additive protocol v1 extension: existing frames, commands, response sizes and
asset format are unchanged. Old hosts may ignore flags. New hosts must check
bit 0 before requesting READ_SCENE and show an unavailable preview on old firmware.

READ_SCENE is read-only: it does not activate, modify or delete content. It reads
the asset header and payload, not arbitrary paths. The request's checksum must
match the current manifest record, otherwise BAD_STATE is returned. Reads during
an upload are rejected with BAD_STATE. Missing ID returns NOT_FOUND; zero or
oversized length and offset at/beyond EOF return BAD_LENGTH. The last response
may be shorter at EOF. On offset zero firmware verifies the whole file. Hosts
must verify echoed offsets, exact assembled size, header/payload CRC and manifest
dimensions/checksum before showing pixels. The implementation uses the existing
bounded response buffer, not a full-screen framebuffer or persistent read session.

Scene metadata commands leave the original LIST_SCENES record layout unchanged.
Hosts check flag bit 1 before querying or changing titles/order. Titles are valid
UTF-8, at most 48 bytes, with no NUL, ASCII control characters or DEL. Empty title
means a generated display name. JSON escaping is handled when saving; quotes,
backslashes and non-ASCII text are supported. GET_SCENE_DETAILS is separate so
old clients can continue to parse LIST_SCENES unchanged.

REORDER_SCENES must include every current ID exactly once, with matching pixel
CRC; stale/missing records return BAD_STATE and malformed permutations return
BAD_LENGTH. It stores order only, not a slideshow. Rename/order are atomically
committed via the existing manifest temporary-file/rename mechanism; reads and
display remain unchanged. Replacing an image through the existing upload commands
preserves its current title and order and still uses A/B files.

Manifest version remains 1; optional `title` fields default to empty for old
records. Firmware 1.2 allows manifests up to 4096 bytes. Downgrading to older
firmware with a 1536-byte parser limit is not guaranteed if a named manifest
exceeds that limit; old host CLI compatibility does not require firmware downgrade.

Each scene record: ID u32, order u32, type u8, width u16, height u16,
pixel-payload checksum u32, active u8, filename length u8, filename ASCII.
Current firmware accepts IDs 1–8 and static images only.

Error codes: 1 BAD_CRC, 2 BAD_MAGIC, 3 UNSUPPORTED_VERSION, 4 NO_SPACE,
5 INVALID_ASSET, 6 INVALID_COMMAND, 7 WRITE_ERROR, 8 BAD_LENGTH,
9 BAD_STATE, 10 NOT_FOUND, 11 STORAGE_ERROR.

Upload chunks must arrive sequentially at the acknowledged offset. BEGIN
opens `/scenes/upload.tmp`. END checks received length, whole-file CRC,
asset header and payload CRC. It then renames the temporary file to the
inactive A/B slot and atomically replaces `manifest.json`. Removing USB before
END leaves the old active slot intact. On boot, stale temporary files are
removed; missing or damaged manifest/asset selects compiled fallback content.

Playlist extension (check capability bit 2): GET_PLAYLIST (15) has an empty
request and returns a 42-byte record. SET_PLAYLIST (16) accepts that record
followed by count expected asset payload checksums (u32, playlist order), and
returns an empty ACK. Missing/stale IDs or upload busy return BAD_STATE;
invalid record/length returns BAD_LENGTH; disk failure returns WRITE_ERROR.
Do not automatically retry writes.

Record: version u8 (=1), enabled u8 (0/1), count u8 (0–8), reserved u8 (=0),
interval_ms u32 (1000–86400000), eight IDs u32 (unique 1–8 in the first count
entries, trailing entries zero), two reserved zero bytes. Enabled requires
at least one ID. GET excludes checksums. SET validates checksums before commit.
Runtime ACTIVE is visible through unchanged LIST_SCENES; ticks do not write
manifest. UPLOAD_BEGIN, SET_ACTIVE_SCENE and DELETE_SCENE stop and persist
playback before their operation; later failure need not resume it.
See [playlist behavior and persistence](playlist.md).

Firmware 1.4 optionally advertises capability bit 3: GET_TRANSITION (17), empty
request, returns one effect byte; SET_PLAYLIST_EFFECT (18) accepts the exact
SET_PLAYLIST payload plus one trailing effect byte (0–5). It validates and
atomically commits the whole playlist with its effect. IDs/checksums/length and
error behavior are the same as command 16; unknown effects return BAD_LENGTH.
Effects: 0 cut with brief backlight blanking, 1 reveal left-to-right, 2 right-to-left,
3 top-to-bottom, 4 bottom-to-top, 5 expand from center. Command 15 is unchanged:
42 bytes, all reserved fields zero. Legacy command 16 selects effect 0. Thus old
hosts keep their original response layouts. New hosts check bit 3 and use 18 for
one atomic settings write; do not chain separate interval/effect commits.

Firmware 1.5 diagnostics (capability byte 7 bit 4): BENCH_ACTIVE (19), empty
request, returns 80 bytes = twenty little-endian u32 values. Record version 1:
version, heap_free_before, largest_block_before, minimum_heap_before,
internal_8bit_free_before, dma_free_before, validate_us, active_id, asset_crc,
width, height, pixel_payload_bytes, read_samples (=5), read_us_total,
render_samples (=3), render_us_total, scratch_bytes (=5120), heap_free_after,
largest_block_after, minimum_heap_after. Timing units are microseconds; heap
values are bytes. It requires a valid registered active static asset, returns
INVALID_ASSET otherwise, and rejects an active upload with BAD_STATE. It does
not mutate files/manifest/settings; it redraws the same active frame without
clearing between repeats and delays the next playlist tick until measurement
ends. Hosts must check bit 4; this flag does not advertise animation support.
See [measured results and planning](animation_planning.md).

Firmware 1.6 animation extension (capability byte7 bit5): asset v1 type2 as
specified in docs/animation_format.md. GET_ASSET_INFO (20) accepts ID u32 and
expected payload CRC u32, returns actual file length u32. READ_SCENE framing is
unchanged; file bounds now include validated type2 files up to512 KiB. Hosts
check type/dimensions and every CRC before preview. UPLOAD_BEGIN_TYPED (21) has
the same 12-byte payload as BEGIN5; new hosts use21 when bit5 is advertised.
Legacy BEGIN5 rejects overwriting an existing animation ID with BAD_STATE.
Static assets remain byte-identical and <=110104 bytes; capabilities max file
is512 KiB for all supported types. A/B commit and v1 framing/sequence/ACK/NACK
are unchanged. Animation playback uses a bounded index and5120-byte strip;
RESET starts at frame0. Older GUIs/firmware do not support mixed animation
collections: use the new desktop version and do not downgrade with type2 scenes.
