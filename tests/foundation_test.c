#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "asset_format.h"
#include "manifest.h"
#include "protocol.h"
#include "upload_guard.h"
#include "wire.h"

static void test_asset(void)
{
    uint8_t file[ASSET_HEADER_SIZE + 4] = {0};
    memcpy(file, "HCAS", 4);
    file[4] = ASSET_VERSION;
    file[5] = ASSET_TYPE_STATIC;
    file[6] = ASSET_PIXEL_RGB565;
    wire_put16(file + 8, 2);
    wire_put16(file + 10, 1);
    wire_put32(file + 12, 4);
    file[24] = 0x00; file[25] = 0xF8;
    file[26] = 0xE0; file[27] = 0x07;
    wire_put32(file + 16, wire_crc32(file + 24, 4));
    wire_put32(file + 20, wire_crc32(file, 20));
    AssetHeader h;
    assert(asset_validate_bytes(file, sizeof(file), &h));
    assert(h.width == 2 && h.height == 1 && h.payload_size == 4);
    file[0] = 'X';
    assert(asset_parse_header(file, sizeof(file), &h) == ASSET_BAD_MAGIC);
    file[0] = 'H'; file[4] = 2;
    assert(asset_parse_header(file, sizeof(file), &h) == ASSET_UNSUPPORTED_VERSION);
    file[4] = ASSET_VERSION;
    file[25] ^= 1;
    assert(!asset_validate_bytes(file, sizeof(file), &h));
    file[25] ^= 1;
    assert(!asset_validate_bytes(file, sizeof(file) - 1, &h));
    wire_put16(file + 8, 321);
    wire_put32(file + 20, wire_crc32(file, 20));
    assert(asset_parse_header(file, sizeof(file), &h) == ASSET_INVALID);
}

static void test_protocol(void)
{
    ProtocolFrame request = {.command = CMD_PING, .sequence = 345, .length = 3,
                             .payload = {'a', 'b', 'c'}};
    uint8_t wire[PROTOCOL_MAX_FRAME];
    const size_t n = protocol_encode(wire, sizeof(wire), &request);
    assert(n == 17);
    ProtocolParser parser = {0};
    ProtocolFrame decoded;
    ProtocolError error;
    uint16_t sequence;
    uint8_t command;
    for (size_t i = 0; i < n - 1; ++i)
        assert(protocol_feed(&parser, wire[i], &decoded, &error,
                             &sequence, &command) == PARSE_MORE);
    assert(protocol_feed(&parser, wire[n - 1], &decoded, &error,
                         &sequence, &command) == PARSE_FRAME);
    assert(decoded.sequence == 345 && decoded.command == CMD_PING &&
           decoded.length == 3 && memcmp(decoded.payload, "abc", 3) == 0);
    wire[n - 1] ^= 1;
    for (size_t i = 0; i < n - 1; ++i)
        assert(protocol_feed(&parser, wire[i], &decoded, &error,
                             &sequence, &command) == PARSE_MORE);
    assert(protocol_feed(&parser, wire[n - 1], &decoded, &error,
                         &sequence, &command) == PARSE_ERROR && error == ERR_BAD_CRC);
    protocol_parser_reset(&parser);
    wire_put16(wire + 8, PROTOCOL_MAX_PAYLOAD + 1);
    for (size_t i = 0; i < 9; ++i)
        assert(protocol_feed(&parser, wire[i], &decoded, &error,
                             &sequence, &command) == PARSE_MORE);
    assert(protocol_feed(&parser, wire[9], &decoded, &error,
                         &sequence, &command) == PARSE_ERROR && error == ERR_BAD_LENGTH);
}

static void test_manifest(void)
{
    Manifest m = {0};
    ManifestScene second = { .id=2, .order=2, .type=1, .width=200,
                             .height=100, .checksum=123, .filename="scenes/0002a.bin" };
    ManifestScene first = { .id=1, .order=1, .type=1, .width=24,
                            .height=24, .checksum=456, .filename="scenes/0001a.bin" };
    assert(manifest_upsert(&m, &second));
    assert(manifest_upsert(&m, &first));
    assert(m.scenes[0].id == 1 && m.scenes[1].id == 2);
    m.active = 1;
    char json[MANIFEST_MAX_BYTES];
    const size_t length = manifest_encode(&m, json, sizeof(json));
    Manifest parsed;
    assert(length > 0 && manifest_parse(json, length, &parsed));
    assert(parsed.active == 1 && parsed.count == 2 && parsed.scenes[0].id == 1);
    json[length] = 'X';
    assert(!manifest_parse(json, length + 1, &parsed));
    json[4] = 'X';
    assert(!manifest_parse(json, length, &parsed));
    assert(!manifest_parse("{", 1, &parsed));
    assert(manifest_delete(&m, 1) && m.active == 0 && m.count == 1);
    const uint8_t title[] = {0xD0, 0xA2, '"', '\\', 'x'};
    assert(manifest_rename(&parsed, 1, title, sizeof(title)));
    const size_t titled_length = manifest_encode(&parsed, json, sizeof(json));
    Manifest titled;
    assert(titled_length && manifest_parse(json, titled_length, &titled));
    assert(memcmp(titled.scenes[0].title, title, sizeof(title)) == 0);
    const uint8_t invalid_utf8[] = {0xC0, 0xAF};
    assert(!manifest_title_valid(invalid_utf8, sizeof(invalid_utf8)));
    assert(!manifest_title_valid((const uint8_t *)"bad\nname", 8));
    const uint32_t order[] = {2, 1};
    assert(manifest_reorder(&titled, order, 2));
    assert(titled.scenes[0].id == 2 && titled.scenes[1].id == 1 && titled.active == 1);
    const uint32_t duplicate[] = {1, 1};
    assert(!manifest_reorder(&titled, duplicate, 2));
    const char legacy[] = "{\"version\":1,\"active\":1,\"scenes\":[{\"id\":1,\"file\":\"scenes/0001a.bin\",\"order\":0,\"type\":1,\"width\":2,\"height\":1,\"checksum\":42}]}";
    assert(manifest_parse(legacy, sizeof(legacy) - 1, &titled));
    assert(titled.scenes[0].title[0] == 0);
}

static void test_interrupted_upload(void)
{
    const uint8_t data[] = {1, 2, 3, 4};
    UploadGuard guard;
    upload_guard_begin(&guard, sizeof(data), wire_crc32(data, sizeof(data)));
    assert(upload_guard_accept(&guard, 0, data, 2));
    assert(!upload_guard_complete(&guard));
    assert(!upload_guard_accept(&guard, 0, data + 2, 2)); // Wrong offset.
    upload_guard_abort(&guard); // Simulates reset while temporary file exists.
    assert(!upload_guard_complete(&guard));
    upload_guard_begin(&guard, sizeof(data), wire_crc32(data, sizeof(data)));
    assert(upload_guard_accept(&guard, 0, data, sizeof(data)));
    assert(upload_guard_complete(&guard));
}

int main(void)
{
    test_asset();
    test_protocol();
    test_manifest();
    test_interrupted_upload();
    puts("firmware foundation tests: OK");
    return 0;
}
