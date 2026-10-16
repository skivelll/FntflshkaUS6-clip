#include "animation.h"
#include "compression.h"
#include "display.h"
#include "wire.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>

typedef struct {
    uint16_t count, width, height, x, y;
    uint32_t bytes, minimum;
} AnimationIndex;
static AnimationIndex active;
static FILE *playing;
static unsigned frame;
static uint32_t began, duration;
static AssetHeader active_header;
static bool compressed;

static bool read_index(FILE *file, const AssetHeader *h, AnimationIndex *index)
{
    uint8_t p[16];
    if (fseek(file, 24, SEEK_SET) || fread(p, 1, 16, file) != 16) return false;
    const uint32_t bytes = (uint32_t)h->width * h->height * 2;
    const uint16_t count = wire_u16(p), x = wire_u16(p + 4), y = wire_u16(p + 6);
    if (count < 2 || p[2] || p[3] || wire_u32(p + 12) ||
        x > 320U - h->width || y > 172U - h->height || wire_u32(p + 8) != bytes ||
        h->payload_size != 16ULL + (uint64_t)count * (12ULL + bytes)) return false;
    *index = (AnimationIndex){count, h->width, h->height, x, y, bytes,
        h->width <= 96 && h->height <= 96 ? 170 :
        (h->width <= 128 && h->height <= 128 ? 250 : 350)};
    return true;
}

static bool read_entry(FILE *file, const AnimationIndex *index, unsigned i,
                       uint32_t *offset, uint32_t *ms, uint32_t *checksum)
{
    uint8_t entry[12];
    if (i >= index->count || fseek(file, 40U + i * 12U, SEEK_SET) ||
        fread(entry, 1, 12, file) != 12) return false;
    *offset = wire_u32(entry); *ms = wire_u32(entry + 4); *checksum = wire_u32(entry + 8);
    return *offset == 16U + index->count * 12U + i * index->bytes &&
           *ms >= index->minimum && *ms <= 60000;
}

bool animation_validate(FILE *file, const AssetHeader *header)
{
    uint8_t encoding;
    if (fseek(file,27,SEEK_SET) || fread(&encoding,1,1,file)!=1) return false;
    if (encoding==1) return compression_validate(file,header);
    AnimationIndex index;
    if (!read_index(file, header, &index)) return false;
    uint8_t block[512];
    // Validate small table pages, then contiguous frame bytes. This avoids two
    // random Flash seeks per tiny frame while keeping RAM independent of count.
    uint8_t entries[32][12];
    for (unsigned base = 0; base < index.count; base += 32) {
        if (base && base % 256U == 0) vTaskDelay(1);
        const unsigned count = index.count - base < 32 ? index.count - base : 32;
        if (fseek(file, 40U + base * 12U, SEEK_SET) ||
            fread(entries, 12, count, file) != count) return false;
        for (unsigned j = 0; j < count; ++j) {
            const uint32_t offset = 16U + index.count * 12U + (base + j) * index.bytes;
            if (wire_u32(entries[j]) != offset || wire_u32(entries[j] + 4) < index.minimum ||
                wire_u32(entries[j] + 4) > 60000) return false;
        }
        if (fseek(file, 24U + wire_u32(entries[0]), SEEK_SET)) return false;
        for (unsigned j = 0; j < count; ++j) {
            uint32_t left = index.bytes, crc = 0xFFFFFFFFU;
            while (left) {
                const size_t n = left < sizeof(block) ? left : sizeof(block);
                if (fread(block, 1, n, file) != n) return false;
                crc = wire_crc32_update(crc, block, n); left -= n;
            }
            if (~crc != wire_u32(entries[j] + 8)) return false;
        }
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
    if (compressed) return compression_draw(playing,&active_header,frame,&duration);
    uint32_t offset, checksum;
    return read_entry(playing, &active, frame, &offset, &duration, &checksum) &&
           !fseek(playing, 24U + offset, SEEK_SET) &&
           display_update_rgb565_at(playing, active.width, active.height, active.x, active.y);
}

bool animation_start(FILE *file, const AssetHeader *header)
{
    animation_stop();
    uint8_t p[16];
    if (fseek(file,24,SEEK_SET) || fread(p,1,16,file)!=16) { fclose(file); return false; }
    compressed=p[3]==1; active_header=*header;
    if (compressed) {
        if (!compression_validate(file,header)) { fclose(file); return false; }
        active.count=wire_u16(p);
    } else if (!read_index(file, header, &active)) { fclose(file); return false; }
    playing = file; frame = 0;
    display_fill(0);
    began = (uint32_t)(esp_timer_get_time() / 1000);
    if (!draw_frame()) { animation_stop(); return false; }
    return true;
}

bool animation_tick(uint32_t now)
{
    if (!playing || (uint32_t)(now - began) < duration) return true;
    frame = (frame + 1) % active.count;
    if (!draw_frame()) { animation_stop(); return false; }
    began = now; // frame durations are start-to-start; never queue catch-up frames
    return true;
}
