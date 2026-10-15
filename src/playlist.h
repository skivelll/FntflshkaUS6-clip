#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PLAYLIST_MAX 8
#define PLAYLIST_BYTES 42
typedef struct {
    bool enabled;
    uint8_t count;
    uint32_t interval_ms;
    uint32_t ids[PLAYLIST_MAX];
    uint8_t transition;
} Playlist;
bool playlist_decode(const uint8_t *data, size_t size, Playlist *out);
void playlist_encode(const Playlist *value, uint8_t *out);
void playlist_init(uint32_t now);
const Playlist *playlist_get(void);
int playlist_set(const Playlist *value, uint32_t now);
void playlist_pause(uint32_t now);
bool playlist_tick(uint32_t now);
