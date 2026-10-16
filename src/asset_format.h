#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define ASSET_HEADER_SIZE 24U
#define ASSET_VERSION 1U
#define ASSET_TYPE_STATIC 1U
#define ASSET_TYPE_ANIMATION 2U
#define ASSET_PIXEL_RGB565 1U
#define ASSET_MAX_WIDTH 320U
#define ASSET_MAX_HEIGHT 172U
#define ASSET_MAX_PAYLOAD (ASSET_MAX_WIDTH * ASSET_MAX_HEIGHT * 2U)
#include "capacity.h"
#define ASSET_MAX_FILE LIBRARY_MAX_BYTES

typedef struct {
    uint16_t width;
    uint16_t height;
    uint32_t payload_size;
    uint32_t payload_crc;
    uint8_t type;
} AssetHeader;

typedef enum {
    ASSET_OK = 0,
    ASSET_BAD_MAGIC,
    ASSET_UNSUPPORTED_VERSION,
    ASSET_INVALID,
    ASSET_BAD_CRC,
} AssetStatus;

AssetStatus asset_parse_header(const uint8_t data[ASSET_HEADER_SIZE],
                               uint32_t file_size, AssetHeader *out);
bool asset_validate_bytes(const uint8_t *data, size_t size, AssetHeader *out);
