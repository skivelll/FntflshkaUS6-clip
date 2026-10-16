#include <stdlib.h>
#include <string.h>
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "asset_format.h"
#include "diagnostics.h"
#include "display.h"
#include "storage.h"
#include "wire.h"
#include "animation.h"
#include "compression.h"

ProtocolError diagnostics_animation(uint8_t *out, size_t *size)
{
    if (storage_upload_busy()) return ERR_BAD_STATE;
    ManifestScene scene;
    FILE *file=storage_open_active(&scene);
    if (!file) return ERR_INVALID_ASSET;
    uint8_t header_bytes[24],prefix_bytes[16]; AssetHeader h;
    if (scene.type!=2 || fseek(file,0,SEEK_END)) { fclose(file); return ERR_INVALID_ASSET; }
    const long length=ftell(file);
    if (length<40 || fseek(file,0,SEEK_SET) || fread(header_bytes,1,24,file)!=24 ||
        asset_parse_header(header_bytes,(uint32_t)length,&h)!=ASSET_OK || fread(prefix_bytes,1,16,file)!=16) {
        fclose(file); return ERR_INVALID_ASSET;
    }
    uint32_t values[12]={1,wire_u16(prefix_bytes),0,0,0,0,
        heap_caps_get_free_size(MALLOC_CAP_8BIT),0,0,
        heap_caps_get_largest_free_block(MALLOC_CAP_8BIT),prefix_bytes[3],(uint32_t)h.width*h.height*2};
    int64_t start=esp_timer_get_time();
    bool ok=animation_validate(file,&h);
    values[3]=(uint32_t)(esp_timer_get_time()-start);
    values[2]=values[1]<8 ? values[1] : 8;
    for (unsigned i=0;ok && i<values[2];++i) {
        start=esp_timer_get_time();
        if (prefix_bytes[3]==1) { uint32_t ms; ok=compression_draw(file,&h,i,&ms); }
        else {
            const uint32_t offset=24+16+values[1]*12+i*values[11];
            ok=!fseek(file,offset,SEEK_SET) && display_update_rgb565_at(file,h.width,h.height,
                wire_u16(prefix_bytes+4),wire_u16(prefix_bytes+6));
        }
        const uint32_t elapsed=(uint32_t)(esp_timer_get_time()-start);
        values[4]+=elapsed; if (elapsed>values[5]) values[5]=elapsed;
    }
    fclose(file);
    values[7]=heap_caps_get_free_size(MALLOC_CAP_8BIT);
    values[8]=heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT);
    if (!ok) return ERR_INVALID_ASSET;
    for (unsigned i=0;i<12;++i) wire_put32(out+i*4,values[i]);
    *size=sizeof(values); return ERR_NONE;
}

// Explicit, bounded profiling only. Never opens arbitrary paths or writes files.
ProtocolError diagnostics_manifest(uint8_t *out, size_t *size)
{
    if (storage_upload_busy()) return ERR_BAD_STATE;
    uint32_t values[8] = {1, MANIFEST_MAX_SCENES, 0, 0,
        heap_caps_get_free_size(MALLOC_CAP_8BIT), 0,
        heap_caps_get_largest_free_block(MALLOC_CAP_8BIT), 0};
    Manifest *source = calloc(1, sizeof(Manifest)), *parsed = malloc(sizeof(Manifest));
    char *json = malloc(MANIFEST_MAX_BYTES);
    bool ok = source && parsed && json;
    const int64_t started = esp_timer_get_time();
    if (ok) {
        for (uint32_t id = 1; id <= MANIFEST_MAX_SCENES; ++id) {
            ManifestScene scene = {.id=id, .order=id-1, .type=1, .width=1, .height=1,
                .checksum=UINT32_MAX};
            snprintf(scene.filename, sizeof(scene.filename), "scenes/%04ua.bin", (unsigned)id);
            for (size_t j=0; j<MANIFEST_TITLE_MAX; ++j) scene.title[j] = j%2 ? '\\' : '"';
            if (!manifest_upsert(source, &scene)) { ok=false; break; }
        }
        source->active=MANIFEST_MAX_SCENES;
        const size_t length = ok ? manifest_encode(source, json, MANIFEST_MAX_BYTES) : 0;
        values[2]=(uint32_t)length;
        ok = length && manifest_parse(json, length, parsed) &&
             parsed->count == source->count && parsed->active == source->active;
        if (ok) for (size_t i=0; i<source->count; ++i)
            if (parsed->scenes[i].id != source->scenes[i].id ||
                strcmp(parsed->scenes[i].title, source->scenes[i].title)) { ok=false; break; }
    }
    values[3]=(uint32_t)(esp_timer_get_time()-started);
    free(json); free(parsed); free(source);
    values[5]=heap_caps_get_free_size(MALLOC_CAP_8BIT);
    values[7]=heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT);
    if (!ok) return ERR_NO_SPACE;
    for (size_t i=0; i<8; ++i) wire_put32(out+i*4, values[i]);
    *size=sizeof(values);
    return ERR_NONE;
}

ProtocolError diagnostics_active(uint8_t *out, size_t *size)
{
    if (!storage_ready()) return ERR_STORAGE;
    if (storage_upload_busy()) return ERR_BAD_STATE;
    uint32_t values[20] = {0};
    values[0] = 1; // diagnostic record version, 20 little-endian u32 values
    values[1] = heap_caps_get_free_size(MALLOC_CAP_8BIT);
    values[2] = heap_caps_get_largest_free_block(MALLOC_CAP_8BIT);
    values[3] = heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT);
    values[4] = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    values[5] = heap_caps_get_free_size(MALLOC_CAP_DMA);
    ManifestScene scene;
    int64_t start = esp_timer_get_time();
    FILE *file = storage_open_active(&scene);
    values[6] = (uint32_t)(esp_timer_get_time() - start);
    if (!file) return ERR_INVALID_ASSET;
    if (scene.type != ASSET_TYPE_STATIC) { fclose(file); return ERR_INVALID_ASSET; }
    values[7] = scene.id; values[8] = scene.checksum;
    values[9] = scene.width; values[10] = scene.height;
    values[11] = (uint32_t)scene.width * scene.height * 2;
    values[12] = 5; values[14] = 3; values[16] = 5120;
    uint8_t *scratch = malloc(values[16]);
    if (!scratch) { fclose(file); return ERR_NO_SPACE; }
    ProtocolError result = ERR_NONE;
    start = esp_timer_get_time();
    for (unsigned sample = 0; sample < values[12]; ++sample) {
        if (fseek(file, ASSET_HEADER_SIZE, SEEK_SET) != 0) { result = ERR_STORAGE; break; }
        uint32_t remaining = values[11];
        while (remaining) {
            size_t count = remaining < values[16] ? remaining : values[16];
            if (fread(scratch, 1, count, file) != count) { result = ERR_STORAGE; break; }
            remaining -= count;
        }
        if (result != ERR_NONE) break;
    }
    values[13] = (uint32_t)(esp_timer_get_time() - start);
    free(scratch);
    start = esp_timer_get_time();
    if (result == ERR_NONE) {
        for (unsigned sample = 0; sample < values[14]; ++sample) {
            if (fseek(file, ASSET_HEADER_SIZE, SEEK_SET) != 0 ||
                !display_update_rgb565_stream(file, scene.width, scene.height)) {
                result = ERR_STORAGE; break;
            }
        }
    }
    values[15] = (uint32_t)(esp_timer_get_time() - start);
    fclose(file);
    values[17] = heap_caps_get_free_size(MALLOC_CAP_8BIT);
    values[18] = heap_caps_get_largest_free_block(MALLOC_CAP_8BIT);
    values[19] = heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT);
    if (result == ERR_NONE) {
        for (size_t i = 0; i < 20; ++i) wire_put32(out + i * 4, values[i]);
        *size = sizeof(values);
    }
    return result;
}
