#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "esp_littlefs.h"
#include "esp_log.h"
#include "esp_partition.h"

#include "asset_format.h"
#include "storage.h"
#include "upload_guard.h"
#include "wire.h"

#define STORAGE_ROOT "/littlefs"
#define MANIFEST_PATH STORAGE_ROOT "/manifest.json"
#define MANIFEST_TEMP STORAGE_ROOT "/manifest.tmp"
#define UPLOAD_TEMP STORAGE_ROOT "/scenes/upload.tmp"

static const char *TAG = "STORAGE";
static bool mounted;
static Manifest manifest;
static struct {
    FILE *file;
    uint32_t id;
    UploadGuard guard;
} upload;

static bool partition_is_blank(const esp_partition_t *partition)
{
    uint8_t block[256];
    for (size_t offset = 0; offset < partition->size; offset += sizeof(block)) {
        if (esp_partition_read(partition, offset, block, sizeof(block)) != ESP_OK)
            return false;
        for (size_t i = 0; i < sizeof(block); ++i)
            if (block[i] != 0xFF) return false;
    }
    return true;
}

static bool save_manifest(const Manifest *new_manifest)
{
    char json[MANIFEST_MAX_BYTES];
    const size_t length = manifest_encode(new_manifest, json, sizeof(json));
    if (length == 0) return false;
    FILE *file = fopen(MANIFEST_TEMP, "wb");
    if (!file) return false;
    const bool wrote = fwrite(json, 1, length, file) == length &&
                       fflush(file) == 0 && fsync(fileno(file)) == 0;
    const bool closed = fclose(file) == 0;
    if (!wrote || !closed || rename(MANIFEST_TEMP, MANIFEST_PATH) != 0) {
        unlink(MANIFEST_TEMP);
        return false;
    }
    manifest = *new_manifest;
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
    while (remaining != 0) {
        const size_t count = remaining < sizeof(block) ? remaining : sizeof(block);
        if (fread(block, 1, count, file) != count) return false;
        crc = wire_crc32_update(crc, block, count);
        remaining -= count;
    }
    if (~crc != header.payload_crc || fgetc(file) != EOF) return false;
    if (out) *out = header;
    return true;
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
        char json[MANIFEST_MAX_BYTES];
        const size_t count = fread(json, 1, sizeof(json), file);
        const bool overflow = fgetc(file) != EOF;
        fclose(file);
        if (overflow || !manifest_parse(json, count, &manifest)) {
            ESP_LOGE(TAG, "manifest invalid; using fallback without erasing files");
            manifest = (Manifest){0};
        }
    } else {
        ESP_LOGI(TAG, "manifest absent; using compiled fallback");
    }
    size_t total, free_bytes;
    if (storage_info(&total, &free_bytes))
        ESP_LOGI(TAG, "LittleFS total=%u free=%u", (unsigned)total, (unsigned)free_bytes);
    return true;
}

bool storage_ready(void) { return mounted; }

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
    if (upload.file) return ERR_BAD_STATE;
    if (id == 0 || id > MANIFEST_MAX_SCENES ||
        file_size < ASSET_HEADER_SIZE + 2 || file_size > ASSET_MAX_FILE)
        return ERR_INVALID_ASSET;
    size_t total, free_bytes;
    if (!storage_info(&total, &free_bytes)) return ERR_STORAGE;
    if (free_bytes < (size_t)file_size + 8192U) return ERR_NO_SPACE;
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
        .type = ASSET_TYPE_STATIC, .width = header.width, .height = header.height,
        .checksum = header.payload_crc,
    };
    snprintf(scene.filename, sizeof(scene.filename), "scenes/%04u%c.bin", (unsigned)id, slot);
    Manifest next = manifest;
    if (!manifest_upsert(&next, &scene)) { unlink(UPLOAD_TEMP); return ERR_NO_SPACE; }
    next.active = id;
    char final_path[48];
    snprintf(final_path, sizeof(final_path), STORAGE_ROOT "/%s", scene.filename);
    unlink(final_path); // Only the inactive slot is replaced.
    if (rename(UPLOAD_TEMP, final_path) != 0) {
        unlink(UPLOAD_TEMP);
        return ERR_WRITE;
    }
    if (!save_manifest(&next)) return ERR_WRITE;
    ESP_LOGI(TAG, "scene=%u committed %ux%u file=%s", (unsigned)id,
             header.width, header.height, scene.filename);
    return ERR_NONE;
}

ProtocolError storage_delete(uint32_t id)
{
    if (!mounted) return ERR_STORAGE;
    if (upload.file) return ERR_BAD_STATE;
    Manifest next = manifest;
    if (!manifest_delete(&next, id)) return ERR_NOT_FOUND;
    if (!save_manifest(&next)) return ERR_WRITE;
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
                       header.width == selected->width && header.height == selected->height &&
                       header.payload_crc == selected->checksum &&
                       fseek(file, ASSET_HEADER_SIZE, SEEK_SET) == 0;
    if (!valid) { fclose(file); return NULL; }
    return file;
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
    Manifest next = manifest;
    next.active = id;
    return save_manifest(&next) ? ERR_NONE : ERR_WRITE;
}

FILE *storage_open_active(ManifestScene *scene)
{
    const ManifestScene *selected = manifest_find(&manifest, manifest.active);
    if (!mounted || !selected) return NULL;
    FILE *file = open_verified_scene(selected);
    if (file && scene) *scene = *selected;
    return file;
}
