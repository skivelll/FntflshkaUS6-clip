#include <ctype.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "asset_format.h"
#include "manifest.h"

bool manifest_title_valid(const uint8_t *bytes, size_t length)
{
    if (!bytes || length > MANIFEST_TITLE_MAX) return false;
    for (size_t i = 0; i < length;) {
        uint32_t cp = bytes[i++];
        unsigned extra = 0;
        uint32_t minimum = 0;
        if (cp >= 0xC2 && cp <= 0xDF) { extra = 1; cp &= 0x1F; minimum = 0x80; }
        else if (cp >= 0xE0 && cp <= 0xEF) { extra = 2; cp &= 0x0F; minimum = 0x800; }
        else if (cp >= 0xF0 && cp <= 0xF4) { extra = 3; cp &= 7; minimum = 0x10000; }
        else if (cp >= 0x80) return false;
        if (i + extra > length) return false;
        for (unsigned n = 0; n < extra; ++n) {
            if ((bytes[i] & 0xC0) != 0x80) return false;
            cp = (cp << 6) | (bytes[i++] & 63);
        }
        if (cp < minimum || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF) || cp < 32 || cp == 127)
            return false;
    }
    return true;
}

bool manifest_rename(Manifest *manifest, uint32_t id, const uint8_t *title, size_t length)
{
    if (!manifest_title_valid(title, length)) return false;
    for (size_t i = 0; i < manifest->count; ++i) {
        if (manifest->scenes[i].id == id) {
            memcpy(manifest->scenes[i].title, title, length);
            manifest->scenes[i].title[length] = 0;
            return true;
        }
    }
    return false;
}

bool manifest_reorder(Manifest *manifest, const uint32_t *ids, size_t count)
{
    if (count != manifest->count || !ids) return false;
    Manifest next = *manifest;
    for (size_t i = 0; i < count; ++i) {
        const ManifestScene *scene = manifest_find(manifest, ids[i]);
        if (!scene) return false;
        for (size_t j = 0; j < i; ++j) if (ids[j] == ids[i]) return false;
        next.scenes[i] = *scene;
        next.scenes[i].order = (uint32_t)i;
    }
    *manifest = next;
    return true;
}

static bool json_uint(const cJSON *object, const char *key, uint32_t max,
                      uint32_t *out)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
    if (!cJSON_IsNumber(item) || item->valuedouble < 0 ||
        item->valuedouble > max || item->valuedouble != (double)(uint32_t)item->valuedouble)
        return false;
    *out = (uint32_t)item->valuedouble;
    return true;
}

static bool valid_filename(uint32_t id, const char *name)
{
    char expected[24];
    for (char slot = 'a'; slot <= 'b'; ++slot) {
        snprintf(expected, sizeof(expected), "scenes/%04u%c.bin", (unsigned)id, slot);
        if (strcmp(name, expected) == 0) return true;
    }
    return false;
}

const ManifestScene *manifest_find(const Manifest *manifest, uint32_t id)
{
    for (size_t i = 0; i < manifest->count; ++i)
        if (manifest->scenes[i].id == id) return &manifest->scenes[i];
    return NULL;
}

void manifest_sort(Manifest *manifest)
{
    for (size_t i = 1; i < manifest->count; ++i) {
        const ManifestScene scene = manifest->scenes[i];
        size_t j = i;
        while (j > 0 && (manifest->scenes[j - 1].order > scene.order ||
               (manifest->scenes[j - 1].order == scene.order &&
                manifest->scenes[j - 1].id > scene.id))) {
            manifest->scenes[j] = manifest->scenes[j - 1];
            --j;
        }
        manifest->scenes[j] = scene;
    }
}

bool manifest_upsert(Manifest *manifest, const ManifestScene *scene)
{
    if (scene->id == 0 || scene->id > MANIFEST_MAX_SCENES ||
        (scene->type != ASSET_TYPE_STATIC && scene->type != ASSET_TYPE_ANIMATION) || !scene->width || !scene->height ||
        scene->width > ASSET_MAX_WIDTH || scene->height > ASSET_MAX_HEIGHT ||
        !valid_filename(scene->id, scene->filename)) return false;
    for (size_t i = 0; i < manifest->count; ++i) {
        if (manifest->scenes[i].id == scene->id) {
            manifest->scenes[i] = *scene;
            manifest_sort(manifest);
            return true;
        }
    }
    if (manifest->count == MANIFEST_MAX_SCENES) return false;
    manifest->scenes[manifest->count++] = *scene;
    manifest_sort(manifest);
    return true;
}

bool manifest_delete(Manifest *manifest, uint32_t id)
{
    for (size_t i = 0; i < manifest->count; ++i) {
        if (manifest->scenes[i].id == id) {
            for (size_t j = i + 1; j < manifest->count; ++j)
                manifest->scenes[j - 1] = manifest->scenes[j];
            --manifest->count;
            if (manifest->active == id) manifest->active = 0;
            return true;
        }
    }
    return false;
}

bool manifest_parse(const char *json, size_t length, Manifest *out)
{
    if (json == NULL || out == NULL || length == 0 || length > MANIFEST_MAX_BYTES)
        return false;
    const char *end = NULL;
    cJSON *root = cJSON_ParseWithLengthOpts(json, length, &end, false);
    if (!cJSON_IsObject(root)) { cJSON_Delete(root); return false; }
    while (end < json + length && isspace((unsigned char)*end)) ++end;
    if (end != json + length) { cJSON_Delete(root); return false; }
    Manifest parsed = {0};
    uint32_t version;
    const cJSON *scenes = cJSON_GetObjectItemCaseSensitive(root, "scenes");
    bool ok = json_uint(root, "version", 1, &version) && version == 1 &&
              json_uint(root, "active", MANIFEST_MAX_SCENES, &parsed.active) &&
              cJSON_IsArray(scenes) &&
              cJSON_GetArraySize(scenes) <= (int)MANIFEST_MAX_SCENES;
    for (int i = 0; ok && i < cJSON_GetArraySize(scenes); ++i) {
        const cJSON *item = cJSON_GetArrayItem(scenes, i);
        ManifestScene scene = {0};
        uint32_t type, width, height;
        const cJSON *name = cJSON_GetObjectItemCaseSensitive(item, "file");
        ok = cJSON_IsObject(item) && json_uint(item, "id", MANIFEST_MAX_SCENES, &scene.id) &&
             json_uint(item, "order", UINT32_MAX, &scene.order) &&
             json_uint(item, "type", 255, &type) &&
             json_uint(item, "width", ASSET_MAX_WIDTH, &width) &&
             json_uint(item, "height", ASSET_MAX_HEIGHT, &height) &&
             json_uint(item, "checksum", UINT32_MAX, &scene.checksum) &&
             cJSON_IsString(name) && strlen(name->valuestring) < sizeof(scene.filename);
        if (ok) {
            scene.type = (uint8_t)type;
            scene.width = (uint16_t)width;
            scene.height = (uint16_t)height;
            strcpy(scene.filename, name->valuestring);
            const cJSON *title = cJSON_GetObjectItemCaseSensitive(item, "title");
            if (title) {
                if (!cJSON_IsString(title) || !manifest_title_valid((const uint8_t *)title->valuestring, strlen(title->valuestring))) {
                    ok = false;
                    break;
                }
                strcpy(scene.title, title->valuestring);
            }
            ok = manifest_find(&parsed, scene.id) == NULL && manifest_upsert(&parsed, &scene);
        }
    }
    if (ok && parsed.active != 0) ok = manifest_find(&parsed, parsed.active) != NULL;
    cJSON_Delete(root);
    if (ok) *out = parsed;
    return ok;
}

size_t manifest_encode(const Manifest *manifest, char *out, size_t capacity)
{
    if (!out || capacity == 0) return 0;
    int n = snprintf(out, capacity, "{\"version\":1,\"active\":%u,\"scenes\":[",
                     (unsigned)manifest->active);
    if (n < 0 || (size_t)n >= capacity) return 0;
    size_t used = (size_t)n;
    for (size_t i = 0; i < manifest->count; ++i) {
        const ManifestScene *s = &manifest->scenes[i];
        cJSON *title_value = cJSON_CreateString(s->title);
        if (!title_value) return 0;
        char *title_json = cJSON_PrintUnformatted(title_value);
        cJSON_Delete(title_value);
        if (!title_json) return 0;
        n = snprintf(out + used, capacity - used,
                     "%s{\"id\":%u,\"file\":\"%s\",\"order\":%u,\"type\":%u,\"width\":%u,\"height\":%u,\"checksum\":%u,\"title\":%s}",
                     i ? "," : "", (unsigned)s->id, s->filename,
                     (unsigned)s->order, s->type, s->width, s->height,
                     (unsigned)s->checksum, title_json);
        cJSON_free(title_json);
        if (n < 0 || (size_t)n >= capacity - used) return 0;
        used += (size_t)n;
    }
    n = snprintf(out + used, capacity - used, "]}\n");
    if (n < 0 || (size_t)n >= capacity - used) return 0;
    return used + (size_t)n;
}
