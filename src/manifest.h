#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MANIFEST_MAX_SCENES 8U
#define MANIFEST_MAX_BYTES 1536U

typedef struct {
    uint32_t id;
    uint32_t order;
    uint8_t type;
    uint16_t width;
    uint16_t height;
    uint32_t checksum;
    char filename[24];
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
