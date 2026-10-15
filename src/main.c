#include <stdbool.h>
#include <stdint.h>

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "display.h"
#include "modes.h"
#include "storage.h"
#include "usb_service.h"
#include "playlist.h"

static uint32_t uptime_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

void app_main(void)
{
    display_init();
    storage_init();
    playlist_init(uptime_ms());
    bool fallback_mode = !usb_service_show_active();
    if (fallback_mode) modes_init(uptime_ms());
    usb_service_init();

    while (true) {
        usb_service_poll(&fallback_mode);
        if (playlist_tick(uptime_ms())) {
            fallback_mode = !usb_service_show_transition();
            playlist_pause(uptime_ms());
            if (fallback_mode) modes_init(uptime_ms());
        }
        if (fallback_mode) modes_update(uptime_ms());
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

