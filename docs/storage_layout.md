# Flash layout (configured 4 MB)

| Name | Offset | Size | Purpose |
| --- | ---: | ---: | --- |
| NVS | `0x9000` | 24 KiB | ESP-IDF system storage |
| PHY init | `0xF000` | 4 KiB | ESP-IDF PHY data |
| factory app | `0x10000` | 2048 KiB | firmware, no OTA |
| storage | `0x210000` | 1984 KiB | LittleFS |

Bootloader occupies the lower reserved flash area. The storage partition ends
at `0x400000` (4 MB). The physical chip may report 8 MB, but the firmware and
all content use only the configured 4 MB. LittleFS usable bytes are lower than
the partition size due to filesystem metadata and wear-leveling; query
`clipctl.py info` or `storage` for the actual total/free values.

LittleFS root: `manifest.json` and `scenes/`. Up to eight scene IDs are
supported. Each ID uses A/B files (`0001a.bin`, `0001b.bin`, etc.). The manifest
contains version, active ID and ordered records with ID, filename, order,
type, dimensions and pixel CRC. It is written via `manifest.tmp` and rename.
Old active files are retained until a later replacement or explicit deletion.

The old partition table had a single 1 MiB factory app and no data partition
for user images; it did not have two OTA application slots.
