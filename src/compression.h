#pragma once
#include "asset_format.h"
#include <stdio.h>
bool compression_validate(FILE *file, const AssetHeader *header);
bool compression_validate_bytes(const uint8_t *data, size_t size, const AssetHeader *header);
bool compression_draw(FILE *file, const AssetHeader *header, unsigned frame, uint32_t *duration);
