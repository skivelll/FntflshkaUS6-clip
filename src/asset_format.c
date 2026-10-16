#include <string.h>

#include "asset_format.h"
#include "wire.h"
#include "compression.h"

AssetStatus asset_parse_header(const uint8_t data[ASSET_HEADER_SIZE],
                               uint32_t file_size, AssetHeader *out)
{
    if (memcmp(data, "HCAS", 4) != 0) return ASSET_BAD_MAGIC;
    if (data[4] != ASSET_VERSION) return ASSET_UNSUPPORTED_VERSION;
    if (wire_u32(data + 20) != wire_crc32(data, 20)) return ASSET_BAD_CRC;
    if ((data[5] != ASSET_TYPE_STATIC && data[5] != ASSET_TYPE_ANIMATION) || data[6] != ASSET_PIXEL_RGB565 || data[7] != 0)
        return ASSET_INVALID;
    const uint16_t width = wire_u16(data + 8);
    const uint16_t height = wire_u16(data + 10);
    const uint32_t payload_size = wire_u32(data + 12);
    if (!width || !height || width > ASSET_MAX_WIDTH || height > ASSET_MAX_HEIGHT ||
        (data[5] == ASSET_TYPE_STATIC && payload_size != (uint32_t)width * height * 2U) ||
        (data[5] == ASSET_TYPE_ANIMATION && payload_size < 16) ||
        payload_size > ASSET_MAX_FILE - ASSET_HEADER_SIZE ||
        file_size != ASSET_HEADER_SIZE + payload_size) return ASSET_INVALID;
    if (out != NULL) {
        *out = (AssetHeader){width, height, payload_size, wire_u32(data + 16), data[5]};
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
    if (header.type == ASSET_TYPE_ANIMATION) {
        const uint8_t *p = data + 24;
        if (header.payload_size < 16) return false;
        if (p[3]==1) {
            if (!compression_validate_bytes(data,size,&header)) return false;
            if (out) *out=header;
            return true;
        }
        const uint16_t count = wire_u16(p);
        const uint32_t bytes = (uint32_t)header.width * header.height * 2;
        const uint32_t minimum = header.width <= 96 && header.height <= 96 ? 170 :
                                 (header.width <= 128 && header.height <= 128 ? 250 : 350);
        if (count < 2 || p[2] || p[3] || wire_u32(p + 12) ||
            wire_u16(p + 4) > 320U - header.width || wire_u16(p + 6) > 172U - header.height ||
            wire_u32(p + 8) != bytes || header.payload_size != 16ULL + (uint64_t)count * (12ULL + bytes)) return false;
        for (unsigned i = 0; i < count; ++i) {
            const uint8_t *entry = p + 16 + i * 12;
            const uint32_t offset = 16U + count * 12U + i * bytes;
            if (wire_u32(entry) != offset || wire_u32(entry + 4) < minimum ||
                wire_u32(entry + 4) > 60000 || wire_crc32(p + offset, bytes) != wire_u32(entry + 8)) return false;
        }
    }
    if (out != NULL) *out = header;
    return true;
}
