#include "animation.h"
#include "display.h"
#include "wire.h"
#include "esp_timer.h"
#include <string.h>

typedef struct {
    uint16_t count, width, height, x, y;
    uint32_t offset[128], duration[128], checksum[128];
} AnimationIndex;
static AnimationIndex active;
static FILE *playing;
static unsigned frame;
static uint32_t began;

static bool read_index(FILE *file, const AssetHeader *h, AnimationIndex *index)
{
    uint8_t p[16];
    if (fseek(file, 24, SEEK_SET) || fread(p, 1, 16, file) != 16) return false;
    const uint32_t bytes = (uint32_t)h->width * h->height * 2;
    const uint16_t count = wire_u16(p), x = wire_u16(p + 4), y = wire_u16(p + 6);
    if (count < 2 || count > 128 || p[2] || p[3] || wire_u32(p + 12) ||
        x > 320U - h->width || y > 172U - h->height || wire_u32(p + 8) != bytes ||
        h->payload_size != 16U + count * (12U + bytes)) return false;
    index->count = count; index->width = h->width; index->height = h->height;
    index->x = x; index->y = y;
    const uint32_t minimum = h->width <= 96 && h->height <= 96 ? 170 :
                             (h->width <= 128 && h->height <= 128 ? 250 : 350);
    for (unsigned i = 0; i < count; ++i) {
        uint8_t entry[12];
        if (fread(entry, 1, 12, file) != 12) return false;
        index->offset[i] = wire_u32(entry);
        index->duration[i] = wire_u32(entry + 4);
        index->checksum[i] = wire_u32(entry + 8);
        if (index->offset[i] != 16U + count * 12U + i * bytes ||
            index->duration[i] < minimum || index->duration[i] > 60000) return false;
    }
    return true;
}

bool animation_validate(FILE *file, const AssetHeader *header)
{
    AnimationIndex index;
    if (!read_index(file, header, &index)) return false;
    uint8_t block[512];
    for (unsigned i = 0; i < index.count; ++i) {
        if (fseek(file, 24 + index.offset[i], SEEK_SET)) return false;
        uint32_t left = (uint32_t)index.width * index.height * 2, crc = 0xFFFFFFFFU;
        while (left) {
            const size_t n = left < sizeof(block) ? left : sizeof(block);
            if (fread(block, 1, n, file) != n) return false;
            crc = wire_crc32_update(crc, block, n); left -= n;
        }
        if (~crc != index.checksum[i]) return false;
    }
    return true;
}

void animation_stop(void)
{
    if (playing) fclose(playing);
    playing = NULL;
}

static bool draw_frame(void)
{
    return !fseek(playing, 24 + active.offset[frame], SEEK_SET) &&
           display_update_rgb565_at(playing, active.width, active.height, active.x, active.y);
}

bool animation_start(FILE *file, const AssetHeader *header)
{
    animation_stop();
    if (!read_index(file, header, &active)) { fclose(file); return false; }
    playing = file; frame = 0;
    display_fill(0);
    began = (uint32_t)(esp_timer_get_time() / 1000);
    if (!draw_frame()) { animation_stop(); return false; }
    return true;
}

bool animation_tick(uint32_t now)
{
    if (!playing || (uint32_t)(now - began) < active.duration[frame]) return true;
    frame = (frame + 1) % active.count;
    if (!draw_frame()) { animation_stop(); return false; }
    began = now; // frame durations are start-to-start; never queue catch-up frames
    return true;
}
