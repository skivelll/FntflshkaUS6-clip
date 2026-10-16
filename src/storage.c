#include <errno.h>
#include <dirent.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "esp_littlefs.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "asset_format.h"
#include "storage.h"
#include "upload_guard.h"
#include "wire.h"
#include "animation.h"

#define STORAGE_ROOT "/littlefs"
#define MANIFEST_PATH STORAGE_ROOT "/manifest.json"
#define MANIFEST_TEMP STORAGE_ROOT "/manifest.tmp"
#define UPLOAD_TEMP STORAGE_ROOT "/scenes/upload.tmp"

static const char *TAG = "STORAGE";
static bool mounted;
static Manifest manifest;
static Manifest working;
static bool committed_manifest;
static bool manifest_untrusted;
static struct {
    FILE *file;
    uint32_t id;
    UploadGuard guard;
} upload;

static bool partition_is_blank(const esp_partition_t *partition)
{
    uint8_t block[256];
    for (size_t offset = 0; offset < partition->size; offset += sizeof(block)) {
        if (offset && offset % 16384U == 0) vTaskDelay(1);
        if (esp_partition_read(partition, offset, block, sizeof(block)) != ESP_OK)
            return false;
        for (size_t i = 0; i < sizeof(block); ++i)
            if (block[i] != 0xFF) return false;
    }
    return true;
}

static bool save_manifest(const Manifest *new_manifest)
{
    // Never turn an unreadable legacy manifest into an empty trusted one: a
    // later boot's orphan cleanup could otherwise discard recoverable content.
    if (manifest_untrusted) return false;
    char *json = malloc(MANIFEST_MAX_BYTES);
    if (!json) return false;
    const size_t length = manifest_encode(new_manifest, json, MANIFEST_MAX_BYTES);
    if (length == 0) { free(json); return false; }
    FILE *file = fopen(MANIFEST_TEMP, "wb");
    if (!file) { free(json); return false; }
    const bool wrote = fwrite(json, 1, length, file) == length &&
                       fflush(file) == 0 && fsync(fileno(file)) == 0;
    const bool closed = fclose(file) == 0;
    free(json);
    if (!wrote || !closed || rename(MANIFEST_TEMP, MANIFEST_PATH) != 0) {
        unlink(MANIFEST_TEMP);
        return false;
    }
    manifest = *new_manifest;
    committed_manifest = true;
    return true;
}

static bool validate_file(FILE *file, uint32_t size, AssetHeader *out)
{
    uint8_t header_bytes[ASSET_HEADER_SIZE];
    if (fseek(file, 0, SEEK_SET) != 0 ||
        fread(header_bytes, 1, sizeof(header_bytes), file) != sizeof(header_bytes))
        return false;
    AssetHeader header;
    if (asset_parse_header(header_bytes, size, &header) != ASSET_OK) return false;
    uint8_t block[512];
    uint32_t remaining = header.payload_size;
    uint32_t crc = 0xFFFFFFFFU;
    unsigned pages = 0;
    while (remaining != 0) {
        const size_t count = remaining < sizeof(block) ? remaining : sizeof(block);
        if (fread(block, 1, count, file) != count) return false;
        crc = wire_crc32_update(crc, block, count);
        remaining -= count;
        if (++pages % 32U == 0) vTaskDelay(1);
    }
    if (~crc != header.payload_crc || fgetc(file) != EOF) return false;
    if (header.type == ASSET_TYPE_ANIMATION && !animation_validate(file, &header)) return false;
    if (out) *out = header;
    return true;
}

uint32_t storage_scene_size(const ManifestScene *scene)
{
    char path[48]; struct stat info;
    snprintf(path, sizeof(path), STORAGE_ROOT "/%s", scene->filename);
    if (stat(path, &info) || info.st_size < ASSET_HEADER_SIZE || info.st_size > ASSET_MAX_FILE)
        return 0;
    return (uint32_t)info.st_size;
}

bool storage_library_used(uint32_t *used)
{
    uint32_t value = 0;
    for (size_t i = 0; i < manifest.count; ++i) {
        if (i && i % 8U == 0) vTaskDelay(1);
        uint32_t size = storage_scene_size(&manifest.scenes[i]);
        if (!size) return false;
        value += storage_charge(size);
    }
    *used = value;
    return true;
}

uint32_t storage_revision(void)
{
    uint32_t crc = 0xFFFFFFFFU;
    for (size_t i = 0; i < manifest.count; ++i) {
        const ManifestScene *s = &manifest.scenes[i];
        uint8_t fields[12];
        wire_put32(fields, s->id); wire_put32(fields + 4, s->order); wire_put32(fields + 8, s->checksum);
        crc = wire_crc32_update(crc, fields, sizeof(fields));
        crc = wire_crc32_update(crc, (const uint8_t *)s->filename, strlen(s->filename) + 1);
        crc = wire_crc32_update(crc, (const uint8_t *)s->title, strlen(s->title) + 1);
    }
    return ~crc; // runtime ACTIVE intentionally excluded (playlist ticks).
}

static bool unreferenced_scene_path(const char *name, char path[64])
{
    unsigned id; char slot; int end = 0; char canonical[24], relative[32];
    if (sscanf(name, "%u%c.bin%n", &id, &slot, &end) != 2 || name[end] ||
        !id || id > STORAGE_ID_MAX || (slot != 'a' && slot != 'b')) return false;
    snprintf(canonical, sizeof(canonical), "%04u%c.bin", id, slot);
    if (strcmp(canonical, name)) return false;
    snprintf(relative, sizeof(relative), "scenes/%s", canonical);
    const ManifestScene *live = manifest_find(&manifest, id);
    if (live && !strcmp(live->filename, relative)) return false;
    snprintf(path, 64, STORAGE_ROOT "/%s", relative);
    return true;
}

static void cleanup_orphans(void)
{
    if (!committed_manifest) return; // Invalid/absent manifest is never grounds for deletion.
    // The common case has no retired files. Avoid opening/validating every asset
    // on every addition/boot; full validation is required only before deletion.
    DIR *dir = opendir(STORAGE_ROOT "/scenes");
    if (!dir) return;
    struct dirent *entry; unsigned scanned = 0; bool found = false;
    while ((entry = readdir(dir))) {
        if (++scanned % 8U == 0) vTaskDelay(1);
        char path[64];
        if (unreferenced_scene_path(entry->d_name, path)) { found = true; break; }
    }
    closedir(dir);
    if (!found) return;
    for (size_t i = 0; i < manifest.count; ++i) {
        if (i && i % 8U == 0) vTaskDelay(1);
        char path[48]; AssetHeader h;
        snprintf(path, sizeof(path), STORAGE_ROOT "/%s", manifest.scenes[i].filename);
        FILE *f = fopen(path, "rb");
        const uint32_t size = storage_scene_size(&manifest.scenes[i]);
        const bool ok = f && size && validate_file(f, size, &h) && h.payload_crc == manifest.scenes[i].checksum;
        if (f) fclose(f);
        if (!ok) return; // Preserve forensic/recovery files if any referenced content is damaged.
    }
    dir = opendir(STORAGE_ROOT "/scenes");
    if (!dir) return;
    scanned = 0;
    while ((entry = readdir(dir))) {
        if (++scanned % 8U == 0) vTaskDelay(1);
        char path[64];
        if (unreferenced_scene_path(entry->d_name, path) && unlink(path) == 0)
            ESP_LOGI(TAG, "removed committed orphan %s", entry->d_name);
    }
    closedir(dir);
}

bool storage_init(void)
{
    const esp_partition_t *partition = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_LITTLEFS, "storage");
    if (!partition) {
        ESP_LOGE(TAG, "LittleFS partition missing");
        return false;
    }
    const esp_vfs_littlefs_conf_t conf = {
        .base_path = STORAGE_ROOT,
        .partition_label = "storage",
        .format_if_mount_failed = partition_is_blank(partition),
    };
    if (esp_vfs_littlefs_register(&conf) != ESP_OK) {
        ESP_LOGE(TAG, "LittleFS mount failed; existing data preserved");
        return false;
    }
    mounted = true;
    if (mkdir(STORAGE_ROOT "/scenes", 0755) != 0 && errno != EEXIST) {
        ESP_LOGE(TAG, "cannot create scenes directory");
        return false;
    }
    unlink(UPLOAD_TEMP);
    unlink(MANIFEST_TEMP);
    FILE *file = fopen(MANIFEST_PATH, "rb");
    if (file) {
        char *json = malloc(MANIFEST_MAX_BYTES);
        if (!json) { fclose(file); mounted = false; return false; }
        const size_t count = fread(json, 1, MANIFEST_MAX_BYTES, file);
        const bool overflow = fgetc(file) != EOF;
        fclose(file);
        if (overflow || !manifest_parse(json, count, &manifest)) {
            ESP_LOGE(TAG, "manifest invalid; using fallback without erasing files");
            memset(&manifest, 0, sizeof(manifest));
            manifest_untrusted = true;
        } else committed_manifest = true;
        free(json);
    } else {
        ESP_LOGI(TAG, "manifest absent; using compiled fallback");
        DIR *dir = opendir(STORAGE_ROOT "/scenes");
        if (!dir) manifest_untrusted = true;
        else {
            struct dirent *entry;
            while ((entry = readdir(dir)))
                if (strcmp(entry->d_name, ".") && strcmp(entry->d_name, "..")) {
                    manifest_untrusted = true;
                    break;
                }
            closedir(dir);
        }
    }
    if (manifest_untrusted)
        ESP_LOGE(TAG, "manifest recovery required; content writes/cleanup disabled");
    cleanup_orphans();
    size_t total, free_bytes;
    if (storage_info(&total, &free_bytes))
        ESP_LOGI(TAG, "LittleFS total=%u free=%u", (unsigned)total, (unsigned)free_bytes);
    return true;
}

bool storage_ready(void) { return mounted; }
bool storage_upload_busy(void) { return upload.file != NULL; }

bool storage_info(size_t *total, size_t *free_bytes)
{
    size_t used;
    if (!mounted || esp_littlefs_info("storage", total, &used) != ESP_OK) return false;
    *free_bytes = *total > used ? *total - used : 0;
    return true;
}

const Manifest *storage_manifest(void) { return &manifest; }

ProtocolError storage_upload_begin(uint32_t id, uint32_t file_size, uint32_t file_crc)
{
    if (!mounted) return ERR_STORAGE;
    if (manifest_untrusted) return ERR_STORAGE;
    if (upload.file) return ERR_BAD_STATE;
    if (id == 0 || id > MANIFEST_ID_MAX ||
        file_size < ASSET_HEADER_SIZE + 2 || file_size > ASSET_MAX_FILE)
        return ERR_INVALID_ASSET;
    size_t total, free_bytes;
    if (!storage_info(&total, &free_bytes)) return ERR_STORAGE;
    uint32_t used;
    if (!storage_library_used(&used)) return ERR_STORAGE;
    const ManifestScene *old = manifest_find(&manifest, id);
    const uint32_t old_size = old ? storage_scene_size(old) : 0;
    const uint32_t projected = used - storage_charge(old_size) + storage_charge(file_size);
    if ((projected > LIBRARY_MAX_BYTES && !(used > LIBRARY_MAX_BYTES && projected < used)) ||
        (!old && manifest.count >= MANIFEST_MAX_SCENES)) return ERR_NO_SPACE;
    if (free_bytes < (size_t)storage_charge(file_size) + STORAGE_SERVICE_RESERVE) return ERR_NO_SPACE;
    upload.file = fopen(UPLOAD_TEMP, "wb");
    if (!upload.file) return ERR_WRITE;
    upload.id = id;
    upload_guard_begin(&upload.guard, file_size, file_crc);
    return ERR_NONE;
}

ProtocolError storage_upload_chunk(uint32_t offset, const uint8_t *bytes, size_t length)
{
    if (!upload.file) return ERR_BAD_STATE;
    if (!length || length > PROTOCOL_MAX_CHUNK || offset != upload.guard.received ||
        length > upload.guard.expected_size - upload.guard.received) return ERR_BAD_LENGTH;
    if (fwrite(bytes, 1, length, upload.file) != length) return ERR_WRITE;
    if (!upload_guard_accept(&upload.guard, offset, bytes, length)) return ERR_BAD_LENGTH;
    return ERR_NONE;
}

ProtocolError storage_upload_end(void)
{
    if (!upload.file) return ERR_BAD_STATE;
    const uint32_t id = upload.id;
    const uint32_t size = upload.guard.expected_size;
    const bool size_ok = upload_guard_complete(&upload.guard);
    const bool flushed = fflush(upload.file) == 0 && fsync(fileno(upload.file)) == 0;
    const bool closed = fclose(upload.file) == 0;
    upload.file = NULL;
    upload_guard_abort(&upload.guard);
    if (!size_ok || !flushed || !closed) {
        unlink(UPLOAD_TEMP);
        return size_ok ? ERR_WRITE : ERR_BAD_CRC;
    }
    FILE *file = fopen(UPLOAD_TEMP, "rb");
    AssetHeader header;
    const bool valid = file && validate_file(file, size, &header);
    if (file) fclose(file);
    if (!valid) { unlink(UPLOAD_TEMP); return ERR_INVALID_ASSET; }

    const ManifestScene *old = manifest_find(&manifest, id);
    const char slot = old && strchr(old->filename, 'a') ? 'b' : 'a';
    ManifestScene scene = {
        .id = id, .order = old ? old->order : (uint32_t)manifest.count,
        .type = header.type, .width = header.width, .height = header.height,
        .checksum = header.payload_crc,
    };
    snprintf(scene.filename, sizeof(scene.filename), "scenes/%04u%c.bin", (unsigned)id, slot);
    Manifest *next = &working;
    *next = manifest;
    if (!manifest_upsert(next, &scene)) { unlink(UPLOAD_TEMP); return ERR_NO_SPACE; }
    if (old) {
        const size_t length = strlen(old->title);
        if (!manifest_rename(next, id, (const uint8_t *)old->title, length)) {
            unlink(UPLOAD_TEMP);
            return ERR_WRITE;
        }
    }
    next->active = id;
    char final_path[48];
    snprintf(final_path, sizeof(final_path), STORAGE_ROOT "/%s", scene.filename);
    unlink(final_path); // Only the inactive slot is replaced.
    if (rename(UPLOAD_TEMP, final_path) != 0) {
        unlink(UPLOAD_TEMP);
        return ERR_WRITE;
    }
    if (!save_manifest(next)) return ERR_WRITE;
    cleanup_orphans();
    ESP_LOGI(TAG, "scene=%u committed %ux%u file=%s", (unsigned)id,
             header.width, header.height, scene.filename);
    return ERR_NONE;
}

ProtocolError storage_delete(uint32_t id)
{
    if (!mounted) return ERR_STORAGE;
    if (upload.file) return ERR_BAD_STATE;
    Manifest *next = &working;
    *next = manifest;
    if (!manifest_delete(next, id)) return ERR_NOT_FOUND;
    if (!save_manifest(next)) return ERR_WRITE;
    for (char slot = 'a'; slot <= 'b'; ++slot) {
        char path[48];
        snprintf(path, sizeof(path), STORAGE_ROOT "/scenes/%04u%c.bin", (unsigned)id, slot);
        unlink(path);
    }
    return ERR_NONE;
}

static FILE *open_verified_scene(const ManifestScene *selected)
{
    char path[48];
    snprintf(path, sizeof(path), STORAGE_ROOT "/%s", selected->filename);
    FILE *file = fopen(path, "rb");
    if (!file) return NULL;
    struct stat info;
    AssetHeader header;
    const bool valid = fstat(fileno(file), &info) == 0 && info.st_size <= ASSET_MAX_FILE &&
                       validate_file(file, (uint32_t)info.st_size, &header) &&
                       header.width == selected->width && header.height == selected->height && header.type == selected->type &&
                       header.payload_crc == selected->checksum &&
                       fseek(file, ASSET_HEADER_SIZE, SEEK_SET) == 0;
    if (!valid) { fclose(file); return NULL; }
    return file;
}

ProtocolError storage_read_scene(uint32_t id, uint32_t checksum, uint32_t offset,
                                 size_t length, uint8_t *out, size_t *received)
{
    if (!mounted) return ERR_STORAGE;
    if (upload.file) return ERR_BAD_STATE;
    if (!out || !received || !length || length > PROTOCOL_MAX_CHUNK) return ERR_BAD_LENGTH;
    const ManifestScene *selected = manifest_find(&manifest, id);
    if (!selected) return ERR_NOT_FOUND;
    if (selected->checksum != checksum) return ERR_BAD_STATE;
    char path[48];
    snprintf(path, sizeof(path), STORAGE_ROOT "/%s", selected->filename);
    // Validate once at the start; host validates the complete returned asset CRC.
    FILE *file = offset == 0 ? open_verified_scene(selected) : fopen(path, "rb");
    if (!file) return ERR_INVALID_ASSET;
    struct stat info;
    if (fstat(fileno(file), &info) != 0 || info.st_size < ASSET_HEADER_SIZE || info.st_size > ASSET_MAX_FILE || offset >= info.st_size) {
        fclose(file); return ERR_BAD_LENGTH;
    }
    const uint32_t size = (uint32_t)info.st_size;
    const size_t count = length < size - offset ? length : size - offset;
    const bool valid = fstat(fileno(file), &info) == 0 && info.st_size == size &&
                       fseek(file, (long)offset, SEEK_SET) == 0 &&
                       fread(out, 1, count, file) == count;
    fclose(file);
    if (!valid) return ERR_INVALID_ASSET;
    *received = count;
    return ERR_NONE;
}

ProtocolError storage_rename(uint32_t id, uint32_t checksum, const uint8_t *title, size_t length)
{
    if (!mounted) return ERR_STORAGE;
    if (upload.file) return ERR_BAD_STATE;
    const ManifestScene *scene = manifest_find(&manifest, id);
    if (!scene) return ERR_NOT_FOUND;
    if (scene->checksum != checksum) return ERR_BAD_STATE;
    Manifest *next = &working;
    *next = manifest;
    if (!manifest_rename(next, id, title, length)) return ERR_BAD_LENGTH;
    return save_manifest(next) ? ERR_NONE : ERR_WRITE;
}

ProtocolError storage_reorder(const uint32_t *ids, const uint32_t *checksums, size_t count)
{
    if (!mounted) return ERR_STORAGE;
    if (upload.file) return ERR_BAD_STATE;
    if (count != manifest.count) return ERR_BAD_STATE;
    for (size_t i = 0; i < count; ++i) {
        const ManifestScene *scene = manifest_find(&manifest, ids[i]);
        if (!scene || scene->checksum != checksums[i]) return ERR_BAD_STATE;
    }
    Manifest *next = &working;
    *next = manifest;
    if (!manifest_reorder(next, ids, count)) return ERR_BAD_LENGTH;
    return save_manifest(next) ? ERR_NONE : ERR_WRITE;
}

ProtocolError storage_activate(uint32_t id)
{
    if (!mounted) return ERR_STORAGE;
    if (upload.file) return ERR_BAD_STATE;
    const ManifestScene *selected = manifest_find(&manifest, id);
    if (!selected) return ERR_NOT_FOUND;
    FILE *file = open_verified_scene(selected);
    if (!file) return ERR_INVALID_ASSET;
    fclose(file);
    Manifest *next = &working;
    *next = manifest;
    next->active = id;
    return save_manifest(next) ? ERR_NONE : ERR_WRITE;
}

FILE *storage_open_active(ManifestScene *scene)
{
    const ManifestScene *selected = manifest_find(&manifest, manifest.active);
    if (!mounted || !selected) return NULL;
    FILE *file = open_verified_scene(selected);
    if (file && scene) *scene = *selected;
    return file;
}

bool storage_runtime_activate(uint32_t id)
{
    const ManifestScene *scene = manifest_find(&manifest, id);
    if (!mounted || upload.file || !scene) return false;
    FILE *file = open_verified_scene(scene);
    if (!file) return false;
    fclose(file);
    manifest.active = id; // Runtime only: timer ticks must not wear Flash.
    return true;
}
