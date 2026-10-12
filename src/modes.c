#include <assert.h>
#include <stddef.h>

#include "esp_log.h"

#include "assets.h"
#include "display.h"
#include "modes.h"

#define MODE_DURATION_MS 5000U
#define ANIMATION_FRAME_MS 100U
#define ANIMATION_SCALE 3

static const char *TAG = "FNTFLSHKAUS6";
static clip_mode_t current_mode;
static uint32_t mode_started_ms;
static uint32_t frame_started_ms;
static size_t art_index;
static unsigned frame_index;
static int animation_x;

static void show_art(void)
{
    const ArtScene *scene = &art_scenes[art_index];
    const ImageAsset *image = scene->image;
    const int width = image->width * scene->scale;
    const int height = image->height * scene->scale;
    assert(scene->scale > 0 && width <= DISPLAY_WIDTH && height <= DISPLAY_HEIGHT);
    const int x = (DISPLAY_WIDTH - width) / 2;
    const int y = (DISPLAY_HEIGHT - height) / 2;

    display_fill(0x0000);
    if (scene->scale == 1) {
        draw_image(x, y, image);
    } else {
        draw_image_scaled(x, y, image, scene->scale);
    }
    ESP_LOGI(TAG, "mode=ART art=%u asset=%s size=%ux%u scale=%u x=%d y=%d",
             (unsigned)art_index, image->name, image->width, image->height,
             scene->scale, x, y);
}

static void show_animation_frame(uint32_t now_ms)
{
    const int width = heart_art.width * ANIMATION_SCALE;
    const int height = heart_art.height * ANIMATION_SCALE;
    const int y = (DISPLAY_HEIGHT - height) / 2;

    if (frame_index != 0) {
        display_fill_rect(animation_x, y, width, height, 0x0000);
    }
    animation_x = (int)(((now_ms - mode_started_ms) * (DISPLAY_WIDTH - width))
                        / MODE_DURATION_MS);
    draw_image_scaled(animation_x, y, &heart_art, ANIMATION_SCALE);
    ESP_LOGI(TAG, "mode=ANIMATION frame=%u x=%d asset=%s scale=%u",
             frame_index, animation_x, heart_art.name, ANIMATION_SCALE);
}

static void enter_mode(clip_mode_t mode, uint32_t now_ms)
{
    current_mode = mode;
    mode_started_ms = now_ms;
    frame_started_ms = now_ms;
    art_index = 0;
    frame_index = 0;
    animation_x = 0;

    switch (mode) {
    case ART:
        show_art();
        break;
    case TEXT:
        display_show_text("FNTFLSHKAUS6", "CLIP");
        ESP_LOGI(TAG, "mode=TEXT");
        break;
    case ANIMATION:
        display_fill(0x0000);
        show_animation_frame(now_ms);
        break;
    }
}

void modes_init(uint32_t now_ms)
{
    assert(art_scene_count >= 2);
    const ImageAsset *assets[] = { &wide_art, &heart_art };
    for (size_t i = 0; i < sizeof(assets) / sizeof(assets[0]); ++i) {
        const ImageAsset *image = assets[i];
        ESP_LOGI(TAG, "asset=%s size=%ux%u rgb565_bytes=%u",
                 image->name, image->width, image->height,
                 (unsigned)((size_t)image->width * image->height * sizeof(uint16_t)));
    }
    enter_mode(ART, now_ms);
}

void modes_next(uint32_t now_ms)
{
    const clip_mode_t next = current_mode == ANIMATION
                             ? ART : (clip_mode_t)(current_mode + 1);
    enter_mode(next, now_ms);
}

void modes_update(uint32_t now_ms)
{
    // Unsigned subtraction also works across the 32-bit millisecond wraparound.
    if (now_ms - mode_started_ms >= MODE_DURATION_MS) {
        modes_next(now_ms);
        return;
    }

    if (current_mode == ART) {
        const size_t next_art = ((now_ms - mode_started_ms) * art_scene_count)
                                / MODE_DURATION_MS;
        if (next_art != art_index) {
            art_index = next_art;
            show_art();
        }
    } else if (current_mode == ANIMATION &&
               now_ms - frame_started_ms >= ANIMATION_FRAME_MS) {
        ++frame_index;
        frame_started_ms = now_ms;
        show_animation_frame(now_ms);
    }
}
