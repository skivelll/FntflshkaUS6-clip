#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "Vernon_ST7789T/Vernon_ST7789T.h"
#include "display.h"

// Waveshare ESP32-C6-LCD-1.47 official ESP-IDF demo: ST7789.h and LVGL_Driver.c.
#define LCD_Y_OFFSET 34
#define LCD_STRIP_HEIGHT 8
#define LCD_SCLK_GPIO 7
#define LCD_MOSI_GPIO 6
#define LCD_CS_GPIO 14
#define LCD_DC_GPIO 15
#define LCD_RST_GPIO 21
#define LCD_BL_GPIO 22

static const char *TAG = "FNTFLSHKAUS6";
static esp_lcd_panel_handle_t panel;
static SemaphoreHandle_t transfer_done;
static uint16_t *strip;

static bool color_transfer_done(esp_lcd_panel_io_handle_t io,
                                esp_lcd_panel_io_event_data_t *event,
                                void *context)
{
    BaseType_t wake = pdFALSE;
    xSemaphoreGiveFromISR((SemaphoreHandle_t)context, &wake);
    return wake == pdTRUE;
}

static void draw_strip(int x, int y, int width, int height)
{
    ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel,
                    x, y, x + width, y + height, strip));
    if (xSemaphoreTake(transfer_done, pdMS_TO_TICKS(1000)) != pdTRUE) {
        ESP_LOGE(TAG, "LCD SPI transfer timed out");
        abort();
    }
}

void display_fill(uint16_t rgb565)
{
    display_fill_rect(0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT, rgb565);
}

void display_fill_rect(int x, int y, int width, int height, uint16_t rgb565)
{
    if (width <= 0 || height <= 0) {
        return;
    }
    const int x1 = x < 0 ? 0 : x;
    const int y1 = y < 0 ? 0 : y;
    const int x2 = x + width > DISPLAY_WIDTH ? DISPLAY_WIDTH : x + width;
    const int y2 = y + height > DISPLAY_HEIGHT ? DISPLAY_HEIGHT : y + height;
    if (x1 >= x2 || y1 >= y2) {
        return;
    }

    // Waveshare's ESP-IDF example sends native 16-bit lv_color_t pixels directly
    // to esp_lcd_panel_draw_bitmap() with this panel configuration.
    const int clipped_width = x2 - x1;
    for (int row_y = y1; row_y < y2; row_y += LCD_STRIP_HEIGHT) {
        const int rows = y2 - row_y < LCD_STRIP_HEIGHT
                         ? y2 - row_y : LCD_STRIP_HEIGHT;
        for (int i = 0; i < clipped_width * rows; ++i) {
            strip[i] = rgb565;
        }
        draw_strip(x1, row_y, clipped_width, rows);
    }
}

void draw_image_scaled(int x, int y, const ImageAsset *asset, uint8_t scale)
{
    assert(asset != NULL && asset->pixels != NULL && scale > 0);
    assert(asset->width > 0 && asset->height > 0);
    const int image_width = asset->width * scale;
    const int image_height = asset->height * scale;
    const int x1 = x < 0 ? 0 : x;
    const int y1 = y < 0 ? 0 : y;
    const int x2 = x + image_width > DISPLAY_WIDTH
                   ? DISPLAY_WIDTH : x + image_width;
    const int y2 = y + image_height > DISPLAY_HEIGHT
                   ? DISPLAY_HEIGHT : y + image_height;
    if (x1 >= x2 || y1 >= y2) {
        return;
    }

    const int clipped_width = x2 - x1;
    for (int row_y = y1; row_y < y2; row_y += LCD_STRIP_HEIGHT) {
        const int rows = y2 - row_y < LCD_STRIP_HEIGHT
                         ? y2 - row_y : LCD_STRIP_HEIGHT;
        for (int row = 0; row < rows; ++row) {
            const size_t source_y = (size_t)(row_y + row - y) / scale;
            for (int col = 0; col < clipped_width; ++col) {
                const size_t source_x = (size_t)(x1 + col - x) / scale;
                const uint16_t color = asset->pixels[source_y * asset->width + source_x];
                strip[row * clipped_width + col] = color;
            }
        }
        draw_strip(x1, row_y, clipped_width, rows);
    }
}

void draw_image(int x, int y, const ImageAsset *asset)
{
    draw_image_scaled(x, y, asset, 1);
}

bool display_draw_rgb565_stream(FILE *file, uint16_t width, uint16_t height)
{
    if (!file || !width || !height || width > DISPLAY_WIDTH || height > DISPLAY_HEIGHT)
        return false;
    const int x = (DISPLAY_WIDTH - width) / 2;
    const int y = (DISPLAY_HEIGHT - height) / 2;
    display_fill(0x0000);
    for (int row = 0; row < height; row += LCD_STRIP_HEIGHT) {
        const int rows = height - row < LCD_STRIP_HEIGHT ? height - row : LCD_STRIP_HEIGHT;
        const size_t count = (size_t)width * rows;
        if (fread(strip, sizeof(uint16_t), count, file) != count) return false;
        draw_strip(x, y + row, width, rows);
    }
    return true;
}

typedef struct {
    char ch;
    uint8_t rows[7];
} glyph_t;

static const glyph_t glyphs[] = {
    {'F', {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10}},
    {'N', {0x11, 0x19, 0x15, 0x15, 0x13, 0x11, 0x11}},
    {'S', {0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E}},
    {'K', {0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11}},
    {'U', {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E}},
    {'6', {0x0E, 0x10, 0x10, 0x1E, 0x11, 0x11, 0x0E}},
    {'H', {0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11}},
    {'A', {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11}},
    {'I', {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x1F}},
    {'R', {0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11}},
    {'C', {0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E}},
    {'L', {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F}},
    {'P', {0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10}},
    {'V', {0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04}},
    {'1', {0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E}},
    {'T', {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04}},
    {'E', {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F}},
    {'X', {0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11}},
    {'M', {0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11}},
    {'O', {0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E}},
    {'D', {0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E}},
};

static const glyph_t *find_glyph(char ch)
{
    for (size_t i = 0; i < sizeof(glyphs) / sizeof(glyphs[0]); ++i) {
        if (glyphs[i].ch == ch) {
            return &glyphs[i];
        }
    }
    return NULL;
}

static void draw_text_line(const char *label, int top, int scale,
                           uint16_t foreground, uint16_t background)
{
    const size_t length = strlen(label);
    const int width = (int)length * 6 * scale;
    assert(width <= DISPLAY_WIDTH);
    const int left = (DISPLAY_WIDTH - width) / 2;
    const int height = 7 * scale;

    for (int row_y = top; row_y < top + height; row_y += LCD_STRIP_HEIGHT) {
        const int rows = top + height - row_y < LCD_STRIP_HEIGHT
                         ? top + height - row_y : LCD_STRIP_HEIGHT;
        for (int row = 0; row < rows; ++row) {
            const int glyph_row = (row_y + row - top) / scale;
            for (int col = 0; col < width; ++col) {
                const int glyph_index = (col / scale) / 6;
                const int glyph_col = (col / scale) % 6;
                const glyph_t *glyph = find_glyph(label[glyph_index]);
                const bool set = glyph != NULL && glyph_col < 5 &&
                    (glyph->rows[glyph_row] & (1U << (4 - glyph_col)));
                strip[row * width + col] = set
                    ? foreground : background;
            }
        }
        draw_strip(left, row_y, width, rows);
    }
}

void display_show_text(const char *first_line, const char *second_line)
{
    const uint16_t background = 0x0010;
    display_fill(background);
    draw_text_line(first_line, 50, 4, 0xFFFF, background);
    draw_text_line(second_line, 100, 3, 0x07FF, background);
}

static void init_backlight(void)
{
    const ledc_timer_config_t timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_13_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = 5000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer));
    const ledc_channel_config_t channel = {
        .gpio_num = LCD_BL_GPIO,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0,
    };
    ESP_ERROR_CHECK(ledc_channel_config(&channel));
}

void display_init(void)
{
    transfer_done = xSemaphoreCreateBinary();
    assert(transfer_done != NULL);
    strip = heap_caps_malloc(DISPLAY_WIDTH * LCD_STRIP_HEIGHT * sizeof(*strip), MALLOC_CAP_DMA);
    assert(strip != NULL);

    const spi_bus_config_t bus = {
        .sclk_io_num = LCD_SCLK_GPIO,
        .mosi_io_num = LCD_MOSI_GPIO,
        .miso_io_num = -1,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = DISPLAY_WIDTH * LCD_STRIP_HEIGHT * sizeof(*strip),
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));

    const esp_lcd_panel_io_spi_config_t io_config = {
        .cs_gpio_num = LCD_CS_GPIO,
        .dc_gpio_num = LCD_DC_GPIO,
        .pclk_hz = 12 * 1000 * 1000,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
        .spi_mode = 0,
        .trans_queue_depth = 2,
        .on_color_trans_done = color_transfer_done,
        .user_ctx = transfer_done,
    };
    esp_lcd_panel_io_handle_t io;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST,
                                             &io_config, &io));
    const esp_lcd_panel_dev_st7789t_config_t panel_config = {
        .reset_gpio_num = LCD_RST_GPIO,
        .rgb_endian = LCD_RGB_ENDIAN_BGR,
        .bits_per_pixel = 16,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_st7789t(io, &panel_config, &panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(panel, true));
    ESP_ERROR_CHECK(esp_lcd_panel_mirror(panel, true, true));
    ESP_ERROR_CHECK(esp_lcd_panel_set_gap(panel, 0, LCD_Y_OFFSET));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));
    init_backlight();
    display_fill(0x0000);
    ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, 4096));
    ESP_ERROR_CHECK(ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0));
}
