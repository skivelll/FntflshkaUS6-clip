#include "wire.h"

uint32_t wire_crc32_update(uint32_t crc, const void *data, size_t length)
{
    const uint8_t *p = data;
    for (size_t i = 0; i < length; ++i) {
        crc ^= p[i];
        for (unsigned bit = 0; bit < 8; ++bit) {
            crc = (crc >> 1) ^ (0xEDB88320U & (0U - (crc & 1U)));
        }
    }
    return crc;
}

uint32_t wire_crc32(const void *data, size_t length)
{
    return ~wire_crc32_update(0xFFFFFFFFU, data, length);
}
