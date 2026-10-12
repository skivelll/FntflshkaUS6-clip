#include "upload_guard.h"
#include "wire.h"

void upload_guard_begin(UploadGuard *guard, uint32_t size, uint32_t crc)
{
    *guard = (UploadGuard){true, size, crc, 0, 0xFFFFFFFFU};
}

bool upload_guard_accept(UploadGuard *guard, uint32_t offset,
                         const uint8_t *bytes, size_t length)
{
    if (!guard->active || !bytes || !length || offset != guard->received ||
        length > guard->expected_size - guard->received) return false;
    guard->running_crc = wire_crc32_update(guard->running_crc, bytes, length);
    guard->received += (uint32_t)length;
    return true;
}

bool upload_guard_complete(const UploadGuard *guard)
{
    return guard->active && guard->received == guard->expected_size &&
           ~guard->running_crc == guard->expected_crc;
}

void upload_guard_abort(UploadGuard *guard)
{
    *guard = (UploadGuard){0};
}
