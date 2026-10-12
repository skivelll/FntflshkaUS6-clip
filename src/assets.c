#include "assets.h"

// Generated from tools/generate_assets.py. The const arrays stay in Flash.
#include "generated_assets.inc"

const ImageAsset wide_art = {
    .name = "WIDE_LANDSCAPE",
    .width = 256,
    .height = 128,
    .pixels = wide_pixels,
};

const ImageAsset heart_art = {
    .name = "PIXEL_HEART",
    .width = 24,
    .height = 24,
    .pixels = heart_pixels,
};

const ArtScene art_scenes[] = {
    { .image = &wide_art, .scale = 1 },
    { .image = &heart_art, .scale = 4 },
};

const size_t art_scene_count = sizeof(art_scenes) / sizeof(art_scenes[0]);
