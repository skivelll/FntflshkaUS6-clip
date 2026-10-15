#include "playlist.h"
#include "storage.h"
#include "wire.h"
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#ifndef PLAYLIST_ROOT
#define PLAYLIST_ROOT "/littlefs"
#endif
#define PATH PLAYLIST_ROOT "/playlist.bin"
#define TEMP PLAYLIST_ROOT "/playlist.tmp"
static Playlist current;
static uint32_t started;
static size_t cursor;

bool playlist_decode(const uint8_t *data, size_t size, Playlist *out)
{
    if (size != PLAYLIST_BYTES || data[0] != 1 || data[1] > 1 ||
        data[2] > PLAYLIST_MAX || data[3] != 0) return false;
    Playlist next = {.enabled = data[1], .count = data[2], .interval_ms = wire_u32(data + 4)};
    if (next.interval_ms < 1000 || next.interval_ms > 86400000 ||
        (next.enabled && !next.count)) return false;
    for (size_t i = 0; i < PLAYLIST_MAX; ++i) {
        next.ids[i] = wire_u32(data + 8 + i * 4);
        if (i >= next.count) { if (next.ids[i]) return false; continue; }
        if (!next.ids[i] || next.ids[i] > 8) return false;
        for (size_t j = 0; j < i; ++j) if (next.ids[j] == next.ids[i]) return false;
    }
    if (data[40] || data[41]) return false;
    *out = next;
    return true;
}

void playlist_encode(const Playlist *value, uint8_t *out)
{
    memset(out, 0, PLAYLIST_BYTES);
    out[0] = 1; out[1] = value->enabled; out[2] = value->count;
    wire_put32(out + 4, value->interval_ms);
    for (size_t i = 0; i < value->count; ++i) wire_put32(out + 8 + i * 4, value->ids[i]);
}

static bool persist(const Playlist *value)
{
    uint8_t data[PLAYLIST_BYTES + 4];
    playlist_encode(value, data);
    data[40] = value->transition; // Private on-disk field; legacy USB record stays zero.
    wire_put32(data + PLAYLIST_BYTES, ~wire_crc32_update(0xFFFFFFFFU, data, PLAYLIST_BYTES));
    FILE *file = fopen(TEMP, "wb");
    if (!file) return false;
    bool ok = fwrite(data, 1, sizeof(data), file) == sizeof(data) &&
              fflush(file) == 0 && fsync(fileno(file)) == 0;
    if (fclose(file) != 0) ok = false;
    if (ok && rename(TEMP, PATH) == 0) return true;
    unlink(TEMP);
    return false;
}

void playlist_init(uint32_t now)
{
    current = (Playlist){.interval_ms = 5000}; started = now; cursor = 0;
    if (!storage_ready()) return;
    unlink(TEMP);
    FILE *file = fopen(PATH, "rb");
    if (!file) return;
    uint8_t data[PLAYLIST_BYTES + 4];
    const bool ok = fread(data, 1, sizeof(data), file) == sizeof(data) && fgetc(file) == EOF;
    fclose(file);
    Playlist loaded;
    if (ok && data[40] <= 5 &&
        wire_u32(data + PLAYLIST_BYTES) == ~wire_crc32_update(0xFFFFFFFFU, data, PLAYLIST_BYTES)) {
        const uint8_t effect = data[40];
        data[40] = 0;
        if (playlist_decode(data, PLAYLIST_BYTES, &loaded)) {
            loaded.transition = effect;
            current = loaded;
        }
    }
    // Start at the first playlist scene after boot, without writing manifest.
    if (current.enabled) {
        for (size_t i = 0; i < current.count; ++i)
            if (storage_runtime_activate(current.ids[i])) { cursor = i; break; }
    }
}

const Playlist *playlist_get(void) { return &current; }

int playlist_set(const Playlist *value, uint32_t now)
{
    if (!storage_ready()) return ERR_STORAGE;
    if (storage_upload_busy()) return ERR_BAD_STATE;
    if (!persist(value)) return ERR_WRITE;
    current = *value; started = now; cursor = current.count ? current.count - 1 : 0;
    if (current.enabled) {
        for (size_t i = 0; i < current.count; ++i)
            if (storage_runtime_activate(current.ids[i])) { cursor = i; break; }
    }
    return ERR_NONE;
}

void playlist_pause(uint32_t now) { started = now; }

bool playlist_tick(uint32_t now)
{
    if (!current.enabled || !current.count) return false;
    if (storage_upload_busy()) { started = now; return false; }
    if ((uint32_t)(now - started) < current.interval_ms) return false;
    started = now;
    for (size_t step = 1; step <= current.count; ++step) {
        const size_t index = (cursor + step) % current.count;
        if (current.ids[index] == storage_manifest()->active) continue;
        if (storage_runtime_activate(current.ids[index])) { cursor = index; return true; }
    }
    return false;
}
