#include <assert.h>
#include <stdint.h>
#include <string.h>

#include "assets.h"
#include "display.h"
#include "modes.h"

static const ImageAsset *last_image;
static int last_x, last_y, last_scale;
static int filled_x, filled_y, filled_width, filled_height;
static unsigned image_draws, text_draws, clears;

void display_fill(uint16_t color)
{
    assert(color == 0);
    ++clears;
}

void display_fill_rect(int x, int y, int width, int height, uint16_t color)
{
    assert(color == 0);
    filled_x = x;
    filled_y = y;
    filled_width = width;
    filled_height = height;
}

void draw_image_scaled(int x, int y, const ImageAsset *image, uint8_t scale)
{
    last_image = image;
    last_x = x;
    last_y = y;
    last_scale = scale;
    ++image_draws;
}

void draw_image(int x, int y, const ImageAsset *image)
{
    draw_image_scaled(x, y, image, 1);
}

void display_show_text(const char *first, const char *second)
{
    assert(strcmp(first, "FNTFLSHKAUS6") == 0);
    assert(strcmp(second, "CLIP") == 0);
    ++text_draws;
}

int main(void)
{
    assert(wide_art.width == 256 && wide_art.height == 128);
    assert(heart_art.width == 24 && heart_art.height == 24);
    assert(wide_art.pixels != NULL && heart_art.pixels != NULL);

    modes_init(0);
    assert(last_image == &wide_art && last_scale == 1);
    assert(last_x == 32 && last_y == 22 && image_draws == 1);
    modes_update(2499);
    assert(image_draws == 1);
    modes_update(2500);
    assert(last_image == &heart_art && last_scale == 4);
    assert(last_x == 112 && last_y == 38 && image_draws == 2);
    modes_update(5000);
    assert(text_draws == 1);
    modes_update(9999);
    assert(text_draws == 1);
    modes_update(10000);
    assert(last_image == &heart_art && last_scale == 3);
    assert(last_x == 0 && last_y == 50);
    modes_update(10099);
    assert(last_x == 0);
    modes_update(10100);
    assert(last_x == 4 && last_y == 50);
    assert(filled_x == 0 && filled_y == 50);
    assert(filled_width == 72 && filled_height == 72);
    modes_update(10200);
    assert(last_x == 9 && filled_x == 4);
    modes_update(14900);
    assert(last_x == 243 && last_y == 50);
    modes_update(15000);
    assert(last_image == &wide_art && last_x == 32);

    modes_next(16000); // Later the MODE button can call this transition.
    assert(text_draws == 2);

    modes_init(UINT32_MAX - 1000);
    modes_update(1499); // 2500 ms later, across the timer wraparound.
    assert(last_image == &heart_art && last_scale == 4);
    modes_update(3999); // 5000 ms later.
    assert(text_draws == 3);
    assert(clears >= 5);
    return 0;
}
