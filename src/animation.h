#pragma once
#include "asset_format.h"
#include <stdio.h>
bool animation_validate(FILE *file, const AssetHeader *header);
void animation_stop(void);
bool animation_start(FILE *file, const AssetHeader *header); // takes ownership
bool animation_tick(uint32_t now);
