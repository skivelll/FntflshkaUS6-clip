#include <stdbool.h>
#include <stdint.h>

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "display.h"
#include "modes.h"
#include "storage.h"
#include "usb_service.h"

static uint32_t uptime_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

void app_main(void)
{
    display_init();
    storage_init();
    bool fallback_mode = !usb_service_show_active();
    if (fallback_mode) modes_init(uptime_ms());
    usb_service_init();

    while (true) {
        usb_service_poll(&fallback_mode);
        if (fallback_mode) modes_update(uptime_ms());
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

