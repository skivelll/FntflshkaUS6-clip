#pragma once

#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>

#include "assets.h"

#define DISPLAY_WIDTH 320
#define DISPLAY_HEIGHT 172

void display_init(void);
void display_fill(uint16_t rgb565);
void display_fill_rect(int x, int y, int width, int height, uint16_t rgb565);
void draw_image(int x, int y, const ImageAsset *asset);
void draw_image_scaled(int x, int y, const ImageAsset *asset, uint8_t scale);
bool display_draw_rgb565_stream(FILE *file, uint16_t width, uint16_t height);
void display_show_text(const char *first_line, const char *second_line);

