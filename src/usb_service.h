#pragma once

#include <stdbool.h>

bool usb_service_init(void);
void usb_service_poll(bool *fallback_mode);
bool usb_service_show_active(void);
