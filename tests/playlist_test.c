// Run in a temporary working directory; compile playlist.c with PLAYLIST_ROOT=".".
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "playlist.h"
#include "storage.h"

static Manifest manifest;
static bool busy;
static unsigned activations;
bool storage_ready(void) { return true; }
bool storage_upload_busy(void) { return busy; }
const Manifest *storage_manifest(void) { return &manifest; }
bool storage_runtime_activate(uint32_t id)
{
    if (busy) return false;
    for (size_t i = 0; i < manifest.count; ++i)
        if (manifest.scenes[i].id == id) {
            manifest.active = id; ++activations; return true;
        }
    return false;
}

int main(void)
{
    remove("playlist.bin"); remove("playlist.tmp");
    manifest = (Manifest){.count = 3, .scenes = {{.id = 1}, {.id = 2}, {.id = 3}}};
    playlist_init(0);
    assert(!playlist_get()->enabled);
    Playlist value = {.enabled = true, .count = 3, .interval_ms = 1000, .ids = {3, 1, 2}, .transition = 5};
    uint8_t bytes[PLAYLIST_BYTES];
    playlist_encode(&value, bytes);
    Playlist decoded;
    assert(playlist_decode(bytes, sizeof(bytes), &decoded));
    assert(decoded.ids[0] == 3 && decoded.interval_ms == 1000);
    bytes[3] = 1;
    assert(!playlist_decode(bytes, sizeof(bytes), &decoded));
    playlist_encode(&value, bytes); bytes[12] = 3;
    assert(!playlist_decode(bytes, sizeof(bytes), &decoded)); // duplicate ID
    assert(!playlist_decode(bytes, sizeof(bytes) - 1, &decoded));
    assert(playlist_set(&value, 0) == ERR_NONE);
    assert(manifest.active == 3 && activations == 1);
    assert(!playlist_tick(999));
    assert(playlist_tick(1000) && manifest.active == 1);
    assert(playlist_tick(2000) && manifest.active == 2);
    // Missing/deleted scene 3 is skipped, without writing config or manifest.
    manifest.count = 2;
    assert(playlist_tick(3000) && manifest.active == 1);
    busy = true;
    assert(!playlist_tick(4000));
    assert(playlist_set(&value, 4000) == ERR_BAD_STATE);
    busy = false;
    assert(!playlist_tick(4999));
    assert(playlist_tick(5000) && manifest.active == 2);
    playlist_init(6000); // enabled/order/interval survive restart; first available is 1
    assert(playlist_get()->enabled && playlist_get()->ids[0] == 3 && manifest.active == 1);
    assert(playlist_get()->transition == 5); // private disk effect survives restart
    assert(playlist_set(&value, UINT32_MAX - 499) == ERR_NONE);
    assert(playlist_tick(500) && manifest.active == 2); // timer rollover
    value.enabled = false;
    assert(playlist_set(&value, 600) == ERR_NONE);
    assert(!playlist_tick(50000));
    playlist_init(0);
    assert(!playlist_get()->enabled);
    FILE *file = fopen("playlist.bin", "wb");
    assert(file); fputs("corrupt", file); fclose(file);
    playlist_init(0);
    assert(!playlist_get()->enabled && playlist_get()->count == 0);
    remove("playlist.bin"); remove("playlist.tmp");
    puts("playlist_test: PASS");
    return 0;
}
