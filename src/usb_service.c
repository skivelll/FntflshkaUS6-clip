#include <stdio.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"

#include "asset_format.h"
#include "display.h"
#include "manifest.h"
#include "modes.h"
#include "protocol.h"
#include "storage.h"
#include "usb_service.h"
#include "wire.h"

static const char *TAG = "USB";
#define FIRMWARE_NAME "FntflshkaUS6 clip"
static ProtocolParser parser;
static bool installed;

static uint32_t now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

bool usb_service_show_active(void)
{
    ManifestScene scene;
    FILE *file = storage_open_active(&scene);
    if (!file) return false;
    const bool drawn = display_draw_rgb565_stream(file, scene.width, scene.height);
    fclose(file);
    if (drawn)
        ESP_LOGI(TAG, "active scene=%u size=%ux%u", (unsigned)scene.id,
                 scene.width, scene.height);
    else
        ESP_LOGE(TAG, "active scene=%u read failed", (unsigned)scene.id);
    return drawn;
}

static void send_response(uint16_t sequence, uint8_t request,
                          ProtocolError error, const uint8_t *data, size_t length)
{
    ProtocolFrame response = {
        .command = error == ERR_NONE ? CMD_ACK : CMD_NACK,
        .sequence = sequence,
        .length = error == ERR_NONE ? (uint16_t)(length + 1) : 2,
    };
    response.payload[0] = request;
    if (error == ERR_NONE && length) memcpy(response.payload + 1, data, length);
    if (error != ERR_NONE) response.payload[1] = (uint8_t)error;
    uint8_t bytes[PROTOCOL_MAX_FRAME];
    const size_t count = protocol_encode(bytes, sizeof(bytes), &response);
    if (count) {
        usb_serial_jtag_write_bytes(bytes, count, pdMS_TO_TICKS(1000));
        usb_serial_jtag_wait_tx_done(pdMS_TO_TICKS(1000));
    }
}

static void handle_request(const ProtocolFrame *request, bool *fallback_mode)
{
    uint8_t reply[PROTOCOL_MAX_PAYLOAD - 1] = {0};
    size_t reply_size = 0;
    ProtocolError error = ERR_NONE;
    const uint8_t *p = request->payload;
    const size_t n = request->length;
    switch (request->command) {
    case CMD_HELLO: {
        if (n != 0) { error = ERR_BAD_LENGTH; break; }
        const size_t name_length = sizeof(FIRMWARE_NAME) - 1;
        memcpy(reply, FIRMWARE_NAME, name_length);
        reply[name_length] = 1; // Firmware major
        reply[name_length + 1] = 2; // Firmware minor: scene metadata extension
        reply_size = name_length + 2;
        break;
    }
    case CMD_GET_CAPABILITIES: {
        if (n != 0) { error = ERR_BAD_LENGTH; break; }
        size_t total = 0, free_bytes = 0;
        storage_info(&total, &free_bytes);
        reply[0] = PROTOCOL_VERSION;
        reply[1] = ASSET_VERSION;
        wire_put16(reply + 2, DISPLAY_WIDTH);
        wire_put16(reply + 4, DISPLAY_HEIGHT);
        reply[6] = ASSET_PIXEL_RGB565;
        reply[7] = 3; // bit 0 = READ_SCENE; bit 1 = scene title/order metadata.
        wire_put32(reply + 8, 4U * 1024U * 1024U);
        wire_put32(reply + 12, 0); // Physical size is not reliably distinct from configured size.
        wire_put32(reply + 16, (uint32_t)total);
        wire_put32(reply + 20, (uint32_t)free_bytes);
        wire_put32(reply + 24, ASSET_MAX_FILE);
        wire_put16(reply + 28, PROTOCOL_MAX_CHUNK);
        wire_put16(reply + 30, DISPLAY_WIDTH * 8U * 2U);
        reply_size = 32;
        break;
    }
    case CMD_GET_STORAGE: {
        if (n != 0) { error = ERR_BAD_LENGTH; break; }
        size_t total, free_bytes;
        if (!storage_info(&total, &free_bytes)) { error = ERR_STORAGE; break; }
        wire_put32(reply, (uint32_t)total);
        wire_put32(reply + 4, (uint32_t)free_bytes);
        reply_size = 8;
        break;
    }
    case CMD_LIST_SCENES: {
        if (n != 0) { error = ERR_BAD_LENGTH; break; }
        const Manifest *manifest = storage_manifest();
        reply[0] = (uint8_t)manifest->count;
        reply_size = 1;
        for (size_t i = 0; i < manifest->count; ++i) {
            const ManifestScene *s = &manifest->scenes[i];
            const size_t name_length = strlen(s->filename);
            if (reply_size + 19 + name_length > sizeof(reply)) {
                error = ERR_STORAGE; break;
            }
            uint8_t *record = reply + reply_size;
            wire_put32(record, s->id);
            wire_put32(record + 4, s->order);
            record[8] = s->type;
            wire_put16(record + 9, s->width);
            wire_put16(record + 11, s->height);
            wire_put32(record + 13, s->checksum);
            record[17] = s->id == manifest->active;
            record[18] = (uint8_t)name_length;
            memcpy(record + 19, s->filename, name_length);
            reply_size += 19 + name_length;
        }
        break;
    }
    case CMD_UPLOAD_BEGIN:
        error = n == 12 ? storage_upload_begin(wire_u32(p), wire_u32(p + 4),
                                                wire_u32(p + 8)) : ERR_BAD_LENGTH;
        break;
    case CMD_UPLOAD_CHUNK:
        error = n >= 5 ? storage_upload_chunk(wire_u32(p), p + 4, n - 4)
                       : ERR_BAD_LENGTH;
        break;
    case CMD_UPLOAD_END:
        error = n == 0 ? storage_upload_end() : ERR_BAD_LENGTH;
        if (error == ERR_NONE) {
            *fallback_mode = !usb_service_show_active();
            if (*fallback_mode) modes_init(now_ms());
        }
        break;
    case CMD_DELETE_SCENE:
        error = n == 4 ? storage_delete(wire_u32(p)) : ERR_BAD_LENGTH;
        if (error == ERR_NONE && storage_manifest()->active == 0) {
            *fallback_mode = true;
            modes_init(now_ms());
        }
        break;
    case CMD_SET_ACTIVE_SCENE:
        error = n == 4 ? storage_activate(wire_u32(p)) : ERR_BAD_LENGTH;
        if (error == ERR_NONE) {
            *fallback_mode = !usb_service_show_active();
            if (*fallback_mode) modes_init(now_ms());
        }
        break;
    case CMD_READ_SCENE:
        if (n != 14) { error = ERR_BAD_LENGTH; break; }
        error = storage_read_scene(wire_u32(p), wire_u32(p + 4), wire_u32(p + 8),
                                   wire_u16(p + 12), reply + 4, &reply_size);
        if (error == ERR_NONE) {
            wire_put32(reply, wire_u32(p + 8));
            reply_size += 4;
        }
        break;
    case CMD_GET_SCENE_DETAILS: {
        if (n != 8) { error = ERR_BAD_LENGTH; break; }
        const ManifestScene *scene = manifest_find(storage_manifest(), wire_u32(p));
        if (!scene) { error = ERR_NOT_FOUND; break; }
        if (scene->checksum != wire_u32(p + 4)) { error = ERR_BAD_STATE; break; }
        const size_t length = strlen(scene->title);
        reply[0] = (uint8_t)length;
        memcpy(reply + 1, scene->title, length);
        reply_size = length + 1;
        break;
    }
    case CMD_RENAME_SCENE:
        error = n >= 8 ? storage_rename(wire_u32(p), wire_u32(p + 4), p + 8, n - 8) : ERR_BAD_LENGTH;
        break;
    case CMD_REORDER_SCENES: {
        if (n < 1 || p[0] > MANIFEST_MAX_SCENES || n != 1U + p[0] * 8U) {
            error = ERR_BAD_LENGTH; break;
        }
        uint32_t ids[MANIFEST_MAX_SCENES], checksums[MANIFEST_MAX_SCENES];
        for (size_t i = 0; i < p[0]; ++i) {
            ids[i] = wire_u32(p + 1 + i * 8);
            checksums[i] = wire_u32(p + 5 + i * 8);
        }
        error = storage_reorder(ids, checksums, p[0]);
        break;
    }
    case CMD_PING:
        if (n > sizeof(reply)) { error = ERR_BAD_LENGTH; break; }
        memcpy(reply, p, n);
        reply_size = n;
        break;
    default:
        error = ERR_INVALID_COMMAND;
        break;
    }
    send_response(request->sequence, request->command, error, reply, reply_size);
}

bool usb_service_init(void)
{
    usb_serial_jtag_driver_config_t config = {
        .rx_buffer_size = 2048,
        .tx_buffer_size = 1024,
    };
    if (usb_serial_jtag_driver_install(&config) != ESP_OK) {
        ESP_LOGE(TAG, "USB Serial/JTAG driver install failed");
        return false;
    }
    protocol_parser_reset(&parser);
    installed = true;
    ESP_LOGI(TAG, "%s USB protocol v%u ready", FIRMWARE_NAME, PROTOCOL_VERSION);
    return true;
}

void usb_service_poll(bool *fallback_mode)
{
    if (!installed) return;
    uint8_t input[64];
    const int count = usb_serial_jtag_read_bytes(input, sizeof(input), 0);
    for (int i = 0; i < count; ++i) {
        ProtocolFrame frame;
        ProtocolError error;
        uint16_t sequence;
        uint8_t command;
        const ParseResult result = protocol_feed(&parser, input[i], &frame,
                                                  &error, &sequence, &command);
        if (result == PARSE_FRAME) handle_request(&frame, fallback_mode);
        else if (result == PARSE_ERROR)
            send_response(sequence, command, error, NULL, 0);
    }
}
