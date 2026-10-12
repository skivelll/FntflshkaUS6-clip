#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    bool active;
    uint32_t expected_size;
    uint32_t expected_crc;
    uint32_t received;
    uint32_t running_crc;
} UploadGuard;

void upload_guard_begin(UploadGuard *guard, uint32_t size, uint32_t crc);
bool upload_guard_accept(UploadGuard *guard, uint32_t offset,
                         const uint8_t *bytes, size_t length);
bool upload_guard_complete(const UploadGuard *guard);
void upload_guard_abort(UploadGuard *guard);
