#pragma once
#include <stdint.h>
#define LIBRARY_MAX_BYTES (820U * 1024U)
#define STORAGE_BLOCK_SIZE 4096U
#define STORAGE_SERVICE_RESERVE (160U * 1024U)
#define STORAGE_SCENE_LIMIT (LIBRARY_MAX_BYTES / STORAGE_BLOCK_SIZE)
#define STORAGE_ID_MAX 65535U
static inline uint32_t storage_charge(uint32_t size)
{
    return ((size + STORAGE_BLOCK_SIZE - 1U) / STORAGE_BLOCK_SIZE) * STORAGE_BLOCK_SIZE;
}
