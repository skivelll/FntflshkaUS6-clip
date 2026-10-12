#include <string.h>

#include "protocol.h"
#include "wire.h"

void protocol_parser_reset(ProtocolParser *parser)
{
    parser->received = 0;
    parser->expected = 0;
}

ParseResult protocol_feed(ProtocolParser *parser, uint8_t byte,
                          ProtocolFrame *frame, ProtocolError *error,
                          uint16_t *sequence, uint8_t *command)
{
    if (parser->received == 0 && byte != 'H') {
        *error = ERR_BAD_MAGIC;
        *sequence = 0;
        *command = 0;
        return PARSE_ERROR;
    }
    parser->buffer[parser->received++] = byte;
    const size_t n = parser->received;
    if (n <= 4 && parser->buffer[n - 1] != "HCUP"[n - 1]) {
        protocol_parser_reset(parser);
        *error = ERR_BAD_MAGIC;
        *sequence = 0;
        *command = 0;
        return PARSE_ERROR;
    }
    if (n < PROTOCOL_HEADER_SIZE) return PARSE_MORE;
    const uint8_t *buf = parser->buffer;
    *sequence = wire_u16(buf + 6);
    *command = buf[5];
    if (n == PROTOCOL_HEADER_SIZE) {
        if (buf[4] != PROTOCOL_VERSION) {
            protocol_parser_reset(parser);
            *error = ERR_UNSUPPORTED_VERSION;
            return PARSE_ERROR;
        }
        const uint16_t length = wire_u16(buf + 8);
        if (length > PROTOCOL_MAX_PAYLOAD) {
            protocol_parser_reset(parser);
            *error = ERR_BAD_LENGTH;
            return PARSE_ERROR;
        }
        parser->expected = PROTOCOL_HEADER_SIZE + length + 4U;
    }
    if (n < parser->expected) return PARSE_MORE;
    const uint16_t length = wire_u16(buf + 8);
    const uint32_t received_crc = wire_u32(buf + PROTOCOL_HEADER_SIZE + length);
    if (received_crc != wire_crc32(buf, PROTOCOL_HEADER_SIZE + length)) {
        protocol_parser_reset(parser);
        *error = ERR_BAD_CRC;
        return PARSE_ERROR;
    }
    frame->command = buf[5];
    frame->sequence = *sequence;
    frame->length = length;
    memcpy(frame->payload, buf + PROTOCOL_HEADER_SIZE, length);
    protocol_parser_reset(parser);
    return PARSE_FRAME;
}

size_t protocol_encode(uint8_t *out, size_t capacity, const ProtocolFrame *frame)
{
    if (frame->length > PROTOCOL_MAX_PAYLOAD ||
        capacity < PROTOCOL_HEADER_SIZE + frame->length + 4U) return 0;
    memcpy(out, "HCUP", 4);
    out[4] = PROTOCOL_VERSION;
    out[5] = frame->command;
    wire_put16(out + 6, frame->sequence);
    wire_put16(out + 8, frame->length);
    memcpy(out + PROTOCOL_HEADER_SIZE, frame->payload, frame->length);
    wire_put32(out + PROTOCOL_HEADER_SIZE + frame->length,
               wire_crc32(out, PROTOCOL_HEADER_SIZE + frame->length));
    return PROTOCOL_HEADER_SIZE + frame->length + 4U;
}
