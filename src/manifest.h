#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "capacity.h"
#define MANIFEST_MAX_SCENES STORAGE_SCENE_LIMIT
#define MANIFEST_ID_MAX STORAGE_ID_MAX
#define MANIFEST_MAX_BYTES 65536U
#define MANIFEST_TITLE_MAX 48U

typedef struct {
    uint32_t id;
    uint32_t order;
    uint8_t type;
    uint16_t width;
    uint16_t height;
    uint32_t checksum;
    char filename[24];
    char title[MANIFEST_TITLE_MAX + 1];
} ManifestScene;

typedef struct {
    uint32_t active;
    size_t count;
    ManifestScene scenes[MANIFEST_MAX_SCENES];
} Manifest;

bool manifest_parse(const char *json, size_t length, Manifest *out);
size_t manifest_encode(const Manifest *manifest, char *out, size_t capacity);
const ManifestScene *manifest_find(const Manifest *manifest, uint32_t id);
bool manifest_upsert(Manifest *manifest, const ManifestScene *scene);
bool manifest_delete(Manifest *manifest, uint32_t id);
void manifest_sort(Manifest *manifest);
bool manifest_title_valid(const uint8_t *bytes, size_t length);
bool manifest_rename(Manifest *manifest, uint32_t id, const uint8_t *title, size_t length);
bool manifest_reorder(Manifest *manifest, const uint32_t *ids, size_t count);
