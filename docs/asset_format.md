# FntflshkaUS6 clip asset format v1

Static RGB565 image only. All integers are unsigned little-endian. The file has
a 24-byte header followed by exactly `width * height * 2` bytes of row-major
pixels. Each pixel is a little-endian RGB565 value (R5:G6:B5); alpha is
composited by the converter before encoding.

| Offset | Size | Field | Required value |
| --- | ---: | --- | --- |
| 0 | 4 | magic | ASCII `HCAS` |
| 4 | 1 | version | `1` |
| 5 | 1 | type | `1` = static image |
| 6 | 1 | pixel format | `1` = RGB565 |
| 7 | 1 | reserved | `0` |
| 8 | 2 | width | 1–320 |
| 10 | 2 | height | 1–172 |
| 12 | 4 | payload size | exactly width × height × 2 |
| 16 | 4 | payload CRC32 | IEEE CRC32 of pixel bytes |
| 20 | 4 | header CRC32 | IEEE CRC32 of bytes 0–19 |

Maximum file size is 110104 bytes. The firmware checks both CRCs, dimensions,
type, version, declared size and actual file length before displaying an asset.
The image is centered without stretching. Pixels outside a smaller image are
black. No full-screen framebuffer is allocated; the display streams 8 rows at
a time through a 5120-byte DMA buffer.

Converter: `python3 tools/make_asset.py input.png output.bin`. PNG alpha is
composited on black by default; use `--background RRGGBB` to change it.
Images larger than 320×172 are reduced proportionally and never enlarged.
