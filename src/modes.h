#pragma once

#include <stdint.h>

typedef enum {
    ART,
    TEXT,
    ANIMATION,
} clip_mode_t;

void modes_init(uint32_t now_ms);
void modes_update(uint32_t now_ms);
void modes_next(uint32_t now_ms);

