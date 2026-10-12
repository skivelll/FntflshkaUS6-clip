#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include "manifest.h"
#include "protocol.h"

bool storage_init(void);
bool storage_ready(void);
bool storage_info(size_t *total, size_t *free_bytes);
const Manifest *storage_manifest(void);
ProtocolError storage_upload_begin(uint32_t id, uint32_t file_size, uint32_t file_crc);
ProtocolError storage_upload_chunk(uint32_t offset, const uint8_t *bytes, size_t length);
ProtocolError storage_upload_end(void);
ProtocolError storage_delete(uint32_t id);
ProtocolError storage_activate(uint32_t id);
FILE *storage_open_active(ManifestScene *scene);
