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

Capabilities record: protocol version u8, asset version u8, display width u16,
height u16, pixel format u8, reserved u8, configured Flash bytes u32,
physical Flash bytes u32 (0 if unavailable), LittleFS total bytes u32,
LittleFS free bytes u32, maximum asset file bytes u32, maximum upload chunk
u16, render buffer bytes u16. Applications must use LittleFS free space rather
than physical Flash size to decide whether content fits.

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
