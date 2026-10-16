#pragma once
#include "protocol.h"
ProtocolError diagnostics_active(uint8_t *out, size_t *size);
ProtocolError diagnostics_manifest(uint8_t *out, size_t *size);
ProtocolError diagnostics_animation(uint8_t *out, size_t *size);
