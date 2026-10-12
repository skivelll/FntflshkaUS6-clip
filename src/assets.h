#pragma once

#include <stddef.h>
#include <stdint.h>

typedef struct {
    const char *name;
    uint16_t width;
    uint16_t height;
    const uint16_t *pixels; // Row-major RGB565 data in Flash.
} ImageAsset;

typedef struct {
    const ImageAsset *image;
    uint8_t scale;
} ArtScene;

extern const ImageAsset wide_art;
extern const ImageAsset heart_art;
extern const ArtScene art_scenes[];
extern const size_t art_scene_count;

