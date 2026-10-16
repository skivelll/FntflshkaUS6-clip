#include "compression.h"
#include "wire.h"
#include "display.h"
#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_rom_crc.h"
#endif
#include <string.h>

static void yield_cpu(void)
{
#ifdef ESP_PLATFORM
    vTaskDelay(1);
#endif
}

static void yield_periodic(uint32_t *last)
{
#ifdef ESP_PLATFORM
    const TickType_t now=xTaskGetTickCount();
    if ((TickType_t)(now-*last)>=pdMS_TO_TICKS(50)) {
        vTaskDelay(1); *last=xTaskGetTickCount();
    }
#else
    (void)last;
#endif
}

typedef struct {
    FILE *file; const uint8_t *bytes; uint32_t size,position,cache_start;
    size_t cache_size; uint8_t cache[512];
} Reader;
typedef struct { uint32_t offset,length,ms,crc; uint16_t x,y,w,h; uint8_t codec,key; } Entry;
typedef struct { Reader *reader; uint32_t left,run,pixels,crc; uint8_t pixel[2],codec; } Decoder;

static bool seek(Reader *r, uint32_t offset)
{
    if (offset > r->size) return false;
    r->position = offset; return true;
}
static bool read_bytes(Reader *r, void *out, size_t n)
{
    if (n > r->size - r->position) return false;
    if (!r->file) { memcpy(out,r->bytes+r->position,n); r->position+=n; return true; }
    uint8_t *destination=out;
    while (n) {
        if (r->position<r->cache_start || r->position>=r->cache_start+r->cache_size) {
            const size_t available=r->size-r->position;
            const size_t wanted=available<sizeof(r->cache) ? available : sizeof(r->cache);
            if (fseek(r->file,r->position,SEEK_SET) || fread(r->cache,1,wanted,r->file)!=wanted) return false;
            r->cache_start=r->position; r->cache_size=wanted;
        }
        const size_t at=r->position-r->cache_start, available=r->cache_size-at;
        const size_t take=n<available ? n : available;
        memcpy(destination,r->cache+at,take);
        destination+=take; r->position+=take; n-=take;
    }
    return true;
}

#include "crc_pixel_shift.inc"

static uint32_t shifted_crc(unsigned power,uint32_t crc)
{
    uint32_t result=0;
    for (unsigned bit=0;crc;++bit,crc>>=1) if (crc&1) result^=crc_pixel_shift[power][bit];
    return result;
}

static uint32_t append_pixels(uint32_t crc,const uint8_t pixel[2],uint16_t count)
{
    if (count<32) {
        uint8_t block[64];
        for (unsigned i=0;i<count;++i) memcpy(block+i*2,pixel,2);
#ifdef ESP_PLATFORM
        return ~esp_rom_crc32_le(~crc,block,count*2);
#else
        return wire_crc32_update(crc,block,count*2);
#endif
    }
    uint32_t result=~crc, part=wire_crc32(pixel,2);
    for (unsigned power=0;count;++power,count>>=1) {
        if (count&1) result=shifted_crc(power,result)^part;
        if (count>1) part=shifted_crc(power,part)^part;
    }
    return ~result;
}
static bool prefix(Reader *r, const AssetHeader *h, uint8_t p[16])
{
    return seek(r,24) && read_bytes(r,p,16) && wire_u16(p)>=2 && !p[2] && p[3]==1 &&
        !wire_u32(p+12) && wire_u32(p+8)==(uint32_t)h->width*h->height*2 &&
        wire_u16(p+4)<=320U-h->width && wire_u16(p+6)<=172U-h->height &&
        (uint64_t)wire_u16(p)*h->width*h->height<=32000000ULL &&
        16U+(uint32_t)wire_u16(p)*28U<=h->payload_size;
}
static bool entry(Reader *r, const AssetHeader *h, unsigned i, Entry *e)
{
    uint8_t p[28];
    if (!seek(r,40U+i*28U) || !read_bytes(r,p,28)) return false;
    *e=(Entry){wire_u32(p),wire_u32(p+4),wire_u32(p+8),wire_u32(p+12),
        wire_u16(p+16),wire_u16(p+18),wire_u16(p+20),wire_u16(p+22),p[24],p[25]};
    const uint32_t minimum=h->width<=96 && h->height<=96 ? 170 :
        (h->width<=128 && h->height<=128 ? 250 : 350);
    return !wire_u16(p+26) && e->codec<=1 && e->key<=1 && e->w && e->h &&
        (uint32_t)e->x+e->w<=h->width && (uint32_t)e->y+e->h<=h->height &&
        e->ms>=minimum && e->ms<=60000 && e->length && e->offset<=h->payload_size &&
        e->length<=h->payload_size-e->offset &&
        (!e->key || (!e->x && !e->y && e->w==h->width && e->h==h->height));
}
static bool decode(void *context, uint8_t *out, size_t n)
{
    Decoder *d=context;
    if (n%2 || n/2>d->pixels) return false;
    if (!d->codec) {
        if (n>d->left || !read_bytes(d->reader,out,n)) return false;
        d->left-=n;
    } else {
        for (size_t i=0;i<n;i+=2) {
            if (!d->run) {
                uint8_t p[4];
                if (d->left<4 || !read_bytes(d->reader,p,4)) return false;
                d->left-=4; d->run=wire_u16(p); memcpy(d->pixel,p+2,2);
                if (!d->run || d->run>d->pixels-i/2) return false;
            }
            memcpy(out+i,d->pixel,2); --d->run;
        }
    }
    d->pixels-=n/2;
#ifdef ESP_PLATFORM
    // ROM API accepts/returns finalized standard CRC32; preserve the internal
    // wire.c running-state convention without changing any protocol checksum.
    d->crc=~esp_rom_crc32_le(~d->crc,out,(uint32_t)n);
#else
    d->crc=wire_crc32_update(d->crc,out,n);
#endif
    return d->pixels || (!d->left && !d->run);
}
static bool validate(Reader *r,const AssetHeader *h)
{
    uint8_t p[16],block[512];
    if (!prefix(r,h,p)) return false;
    const unsigned count=wire_u16(p);
    uint32_t expected=16U+count*28U;
    unsigned chain=0;
    uint32_t last_yield=0;
    for (unsigned i=0;i<count;++i) {
        Entry e;
        if (!entry(r,h,i,&e) || e.offset!=expected || (!i && !e.key)) return false;
        chain=e.key ? 0 : chain+1;
        if (chain>=32 || !seek(r,24+e.offset)) return false;
        Decoder d={r,e.length,0,(uint32_t)e.w*e.h,0xFFFFFFFFU,{0},e.codec};
        unsigned pages=0;
        if (e.codec==1) {
            while (d.left) {
                uint8_t run[4];
                if (d.left<4 || !read_bytes(r,run,4)) return false;
                const uint16_t count=wire_u16(run);
                if (!count || count>d.pixels) return false;
                d.left-=4; d.pixels-=count; d.crc=append_pixels(d.crc,run+2,count);
                if (++pages%32==0) yield_periodic(&last_yield);
            }
            if (d.pixels) return false;
        } else while (d.pixels) {
            const size_t n=d.pixels*2<sizeof(block) ? d.pixels*2 : sizeof(block);
            if (!decode(&d,block,n)) return false;
            if (++pages%32==0) yield_periodic(&last_yield);
        }
        if (~d.crc!=e.crc) return false;
        expected+=e.length;
        if (i%32==0) yield_cpu();
    }
    return expected==h->payload_size;
}
bool compression_validate(FILE *file,const AssetHeader *h)
{
    Reader r={.file=file,.size=24+h->payload_size}; return validate(&r,h);
}
bool compression_validate_bytes(const uint8_t *data,size_t size,const AssetHeader *h)
{
    Reader r={.bytes=data,.size=(uint32_t)size}; return validate(&r,h);
}
bool compression_draw(FILE *file,const AssetHeader *h,unsigned frame,uint32_t *duration)
{
#ifdef ESP_PLATFORM
    Reader r={.file=file,.size=24+h->payload_size}; uint8_t p[16]; Entry e;
    if (!prefix(&r,h,p) || frame>=wire_u16(p) || !entry(&r,h,frame,&e) || !seek(&r,24+e.offset)) return false;
    Decoder d={&r,e.length,0,(uint32_t)e.w*e.h,0xFFFFFFFFU,{0},e.codec};
    *duration=e.ms;
    return display_update_decoded(decode,&d,e.w,e.h,wire_u16(p+4)+e.x,wire_u16(p+6)+e.y) && ~d.crc==e.crc;
#else
    (void)file; (void)h; (void)frame; (void)duration;
    return false; // Rendering is intentionally unavailable in native codec tests.
#endif
}
