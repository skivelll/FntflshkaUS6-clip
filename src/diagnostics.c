#include <stdlib.h>
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "asset_format.h"
#include "diagnostics.h"
#include "display.h"
#include "storage.h"
#include "wire.h"

// Explicit, bounded profiling only. Never opens arbitrary paths or writes files.
ProtocolError diagnostics_active(uint8_t *out, size_t *size)
{
    if (!storage_ready()) return ERR_STORAGE;
    if (storage_upload_busy()) return ERR_BAD_STATE;
    uint32_t values[20] = {0};
    values[0] = 1; // diagnostic record version, 20 little-endian u32 values
    values[1] = heap_caps_get_free_size(MALLOC_CAP_8BIT);
    values[2] = heap_caps_get_largest_free_block(MALLOC_CAP_8BIT);
    values[3] = heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT);
    values[4] = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    values[5] = heap_caps_get_free_size(MALLOC_CAP_DMA);
    ManifestScene scene;
    int64_t start = esp_timer_get_time();
    FILE *file = storage_open_active(&scene);
    values[6] = (uint32_t)(esp_timer_get_time() - start);
    if (!file) return ERR_INVALID_ASSET;
    if (scene.type != ASSET_TYPE_STATIC) { fclose(file); return ERR_INVALID_ASSET; }
    values[7] = scene.id; values[8] = scene.checksum;
    values[9] = scene.width; values[10] = scene.height;
    values[11] = (uint32_t)scene.width * scene.height * 2;
    values[12] = 5; values[14] = 3; values[16] = 5120;
    uint8_t *scratch = malloc(values[16]);
    if (!scratch) { fclose(file); return ERR_NO_SPACE; }
    ProtocolError result = ERR_NONE;
    start = esp_timer_get_time();
    for (unsigned sample = 0; sample < values[12]; ++sample) {
        if (fseek(file, ASSET_HEADER_SIZE, SEEK_SET) != 0) { result = ERR_STORAGE; break; }
        uint32_t remaining = values[11];
        while (remaining) {
            size_t count = remaining < values[16] ? remaining : values[16];
            if (fread(scratch, 1, count, file) != count) { result = ERR_STORAGE; break; }
            remaining -= count;
        }
        if (result != ERR_NONE) break;
    }
    values[13] = (uint32_t)(esp_timer_get_time() - start);
    free(scratch);
    start = esp_timer_get_time();
    if (result == ERR_NONE) {
        for (unsigned sample = 0; sample < values[14]; ++sample) {
            if (fseek(file, ASSET_HEADER_SIZE, SEEK_SET) != 0 ||
                !display_update_rgb565_stream(file, scene.width, scene.height)) {
                result = ERR_STORAGE; break;
            }
        }
    }
    values[15] = (uint32_t)(esp_timer_get_time() - start);
    fclose(file);
    values[17] = heap_caps_get_free_size(MALLOC_CAP_8BIT);
    values[18] = heap_caps_get_largest_free_block(MALLOC_CAP_8BIT);
    values[19] = heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT);
    if (result == ERR_NONE) {
        for (size_t i = 0; i < 20; ++i) wire_put32(out + i * 4, values[i]);
        *size = sizeof(values);
    }
    return result;
}
