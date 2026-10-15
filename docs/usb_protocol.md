# FntflshkaUS6 clip USB Serial protocol v1

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
(GET_SCENE_DETAILS/RENAME_SCENE/REORDER_SCENES); other bits are reserved.
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
