#include <string.h>

#include "asset_format.h"
#include "wire.h"

AssetStatus asset_parse_header(const uint8_t data[ASSET_HEADER_SIZE],
                               uint32_t file_size, AssetHeader *out)
{
    if (memcmp(data, "HCAS", 4) != 0) return ASSET_BAD_MAGIC;
    if (data[4] != ASSET_VERSION) return ASSET_UNSUPPORTED_VERSION;
    if (wire_u32(data + 20) != wire_crc32(data, 20)) return ASSET_BAD_CRC;
    if (data[5] != ASSET_TYPE_STATIC || data[6] != ASSET_PIXEL_RGB565 || data[7] != 0)
        return ASSET_INVALID;
    const uint16_t width = wire_u16(data + 8);
    const uint16_t height = wire_u16(data + 10);
    const uint32_t payload_size = wire_u32(data + 12);
    if (!width || !height || width > ASSET_MAX_WIDTH || height > ASSET_MAX_HEIGHT ||
        payload_size != (uint32_t)width * height * 2U ||
        file_size != ASSET_HEADER_SIZE + payload_size) return ASSET_INVALID;
    if (out != NULL) {
        *out = (AssetHeader){width, height, payload_size, wire_u32(data + 16)};
    }
    return ASSET_OK;
}

bool asset_validate_bytes(const uint8_t *data, size_t size, AssetHeader *out)
{
    if (data == NULL || size < ASSET_HEADER_SIZE || size > ASSET_MAX_FILE) return false;
    AssetHeader header;
    if (asset_parse_header(data, (uint32_t)size, &header) != ASSET_OK) return false;
    if (wire_crc32(data + ASSET_HEADER_SIZE, header.payload_size) != header.payload_crc)
        return false;
    if (out != NULL) *out = header;
    return true;
}
