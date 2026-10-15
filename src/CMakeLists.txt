idf_component_register(
    SRCS "main.c" "display.c" "modes.c" "assets.c" "wire.c"
         "asset_format.c" "manifest.c" "protocol.c" "upload_guard.c"
         "storage.c" "usb_service.c" "playlist.c" "diagnostics.c" "animation.c"
         "Vernon_ST7789T/Vernon_ST7789T.c"
    INCLUDE_DIRS "."
    REQUIRES esp_lcd driver joltwallet__littlefs json esp_partition spi_flash
)

