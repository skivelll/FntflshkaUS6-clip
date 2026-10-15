#pragma once

#include <stddef.h>
#include <stdint.h>

#define PROTOCOL_VERSION 1U
#define PROTOCOL_HEADER_SIZE 10U
#define PROTOCOL_MAX_PAYLOAD 520U
#define PROTOCOL_MAX_FRAME (PROTOCOL_HEADER_SIZE + PROTOCOL_MAX_PAYLOAD + 4U)
#define PROTOCOL_MAX_CHUNK 512U

typedef enum {
    CMD_HELLO = 1,
    CMD_GET_CAPABILITIES = 2,
    CMD_GET_STORAGE = 3,
    CMD_LIST_SCENES = 4,
    CMD_UPLOAD_BEGIN = 5,
    CMD_UPLOAD_CHUNK = 6,
    CMD_UPLOAD_END = 7,
    CMD_DELETE_SCENE = 8,
    CMD_SET_ACTIVE_SCENE = 9,
    CMD_PING = 10,
    CMD_READ_SCENE = 11,
    CMD_GET_SCENE_DETAILS = 12,
    CMD_RENAME_SCENE = 13,
    CMD_REORDER_SCENES = 14,
    CMD_ACK = 0x80,
    CMD_NACK = 0x81,
} ProtocolCommand;

typedef enum {
    ERR_NONE = 0,
    ERR_BAD_CRC = 1,
    ERR_BAD_MAGIC = 2,
    ERR_UNSUPPORTED_VERSION = 3,
    ERR_NO_SPACE = 4,
    ERR_INVALID_ASSET = 5,
    ERR_INVALID_COMMAND = 6,
    ERR_WRITE = 7,
    ERR_BAD_LENGTH = 8,
    ERR_BAD_STATE = 9,
    ERR_NOT_FOUND = 10,
    ERR_STORAGE = 11,
} ProtocolError;

typedef struct {
    uint8_t command;
    uint16_t sequence;
    uint16_t length;
    uint8_t payload[PROTOCOL_MAX_PAYLOAD];
} ProtocolFrame;

typedef struct {
    uint8_t buffer[PROTOCOL_MAX_FRAME];
    size_t received;
    size_t expected;
} ProtocolParser;

typedef enum {
    PARSE_MORE = 0,
    PARSE_FRAME = 1,
    PARSE_ERROR = 2,
} ParseResult;

void protocol_parser_reset(ProtocolParser *parser);
ParseResult protocol_feed(ProtocolParser *parser, uint8_t byte,
                          ProtocolFrame *frame, ProtocolError *error,
                          uint16_t *sequence, uint8_t *command);
size_t protocol_encode(uint8_t *out, size_t capacity, const ProtocolFrame *frame);
