"""Lossless animation encoding 1. See docs/animation_compression.md."""
import struct
import zlib
from dataclasses import dataclass
from PIL import Image, ImageChops
from .asset import rgb565_payload
from .animation import PREFIX, MAX_FILE, minimum_duration, encode_animation

ENTRY = struct.Struct('<IIIIHHHHBBH')


@dataclass(frozen=True)
class AnimationEstimate:
    file_size: int
    prefix_sizes: tuple[int, ...]


def rle_encode(data):
    output = bytearray()
    pixel, run = data[:2], 0
    for i in range(0, len(data), 2):
        value = data[i:i+2]
        if value != pixel or run == 65535:
            output.extend(struct.pack('<H',run) + pixel)
            pixel, run = value, 0
        run += 1
    if run:
        output.extend(struct.pack('<H',run) + pixel)
    return bytes(output)


def rectangle_decode(data, codec, pixels):
    if codec == 0:
        if len(data) != pixels*2:
            raise ValueError('invalid raw rectangle length')
        return bytes(data)
    if codec != 1 or len(data) % 4:
        raise ValueError('invalid RLE length/codec')
    result = bytearray()
    for i in range(0,len(data),4):
        count = struct.unpack_from('<H',data,i)[0]
        if not count or len(result)//2 + count > pixels:
            raise ValueError('invalid RLE run')
        result.extend(data[i+2:i+4]*count)
    if len(result) != pixels*2:
        raise ValueError('incomplete RLE rectangle')
    return bytes(result)


def parse_compressed(data,width,height,size,crc):
    count,loop,encoding,x,y,frame_bytes,reserved = PREFIX.unpack_from(data,24)
    if (count < 2 or loop or encoding != 1 or reserved or frame_bytes != width*height*2
            or count*width*height > 32_000_000
            or not 0 <= x <= 320-width or not 0 <= y <= 172-height
            or 16 + count*ENTRY.size > size):
        raise ValueError('invalid compressed animation prefix')
    offset = 16 + count*ENTRY.size
    records = []
    chain = 0
    for i in range(count):
        o,n,ms,checksum,rx,ry,rw,rh,codec,flags,tail = ENTRY.unpack_from(data,40+i*ENTRY.size)
        if (o != offset or not n or o+n > size or tail or flags not in (0,1)
                or not rw or not rh or rx+rw > width or ry+rh > height
                or not minimum_duration(width,height) <= ms <= 60000):
            raise ValueError('invalid compressed animation record')
        if flags:
            if (rx,ry,rw,rh) != (0,0,width,height):
                raise ValueError('invalid key frame')
            chain = 0
        else:
            chain += 1
            if i == 0 or chain >= 32:
                raise ValueError('missing key frame')
        raw = rectangle_decode(data[24+o:24+o+n],codec,rw*rh)
        if zlib.crc32(raw) != checksum:
            raise ValueError('rectangle CRC mismatch')
        records.append((24+o,n,ms,checksum,rx,ry,rw,rh,codec,flags))
        offset += n
    if offset != size:
        raise ValueError('trailing animation data')
    return dict(type=2,encoding=1,width=width,height=height,payload_bytes=size,
                payload_crc=crc,x=x,y=y,frame_bytes=frame_bytes,records=tuple(records),
                frames=tuple((r[0],r[2],r[3]) for r in records))


def decoded_frames(data,info):
    canvas = bytearray(info['frame_bytes'])
    width = info['width']
    for offset,n,ms,crc,x,y,w,h,codec,key in info['records']:
        raw = rectangle_decode(data[offset:offset+n],codec,w*h)
        for row in range(h):
            start = ((y+row)*width+x)*2
            canvas[start:start+w*2] = raw[row*w*2:(row+1)*w*2]
        yield bytes(canvas),ms


def encode_optimized(frames,durations,origin=None,*,measure_only=False):
    if not 2 <= len(frames) <= 65535 or len(frames) != len(durations):
        raise ValueError('Для анимации нужно не менее двух кадров.')
    width,height = frames[0].size
    if len(frames)*width*height > 32_000_000:
        raise ValueError('Выбранные кадры превышают бюджет preview 32 MP. Сократи диапазон или увеличь шаг.')
    if not 1 <= width <= 320 or not 1 <= height <= 172 or any(f.size != (width,height) for f in frames):
        raise ValueError('Неверный размер кадра.')
    x,y = origin or ((320-width)//2,(172-height)//2)
    if not 0 <= x <= 320-width or not 0 <= y <= 172-height:
        raise ValueError('Анимация не помещается на экран.')
    index, chunks = bytearray(), []
    offset, previous = 16+len(frames)*ENTRY.size, None
    prefix_sizes=[]
    stored_total=0
    for i,(image,ms) in enumerate(zip(frames,durations)):
        if type(ms) is not int or not minimum_duration(width,height) <= ms <= 60000:
            raise ValueError('Недопустимое время кадра.')
        raw = rgb565_payload(image)
        # Compare already quantized pixels, not RGB888 differences.
        quantized = Image.frombytes('LA',(width,height),raw)
        full = (0,0,width,height)
        choices = [(raw,0,full,1)]
        choices.append((rle_encode(raw),1,full,1))
        if i % 32 and previous is not None:
            diff = ImageChops.difference(quantized,previous)
            a,b = diff.split()
            bbox = ImageChops.lighter(a,b).getbbox() or (0,0,1,1)
            left,top,right,bottom = bbox
            patch = quantized.crop(bbox).tobytes()
            rect = (left,top,right-left,bottom-top)
            choices.extend(((patch,0,rect,0),(rle_encode(patch),1,rect,0)))
        stored,codec,rect,key = min(choices,key=lambda item:len(item[0]))
        left,top,w,h = rect
        decoded = raw if key else quantized.crop((left,top,left+w,top+h)).tobytes()
        if not measure_only:
            index.extend(ENTRY.pack(offset,len(stored),ms,zlib.crc32(decoded),*rect,codec,key,0))
            chunks.append(stored)
        stored_total+=len(stored)
        prefix_sizes.append(min(40+(i+1)*ENTRY.size+stored_total,40+(i+1)*(12+width*height*2)))
        offset += len(stored)
        previous = quantized
        if not measure_only and offset+24 > MAX_FILE:
            if 40+len(frames)*(12+width*height*2) <= MAX_FILE:
                return encode_animation(frames,durations,origin)
            raise ValueError('Даже после сжатия анимация больше 820 KiB. Сократи диапазон или увеличь шаг.')
    if measure_only:
        return AnimationEstimate(prefix_sizes[-1],tuple(prefix_sizes))
    payload = PREFIX.pack(len(frames),0,1,x,y,width*height*2,0)+index+b''.join(chunks)
    header = struct.pack('<4sBBBBHHII',b'HCAS',1,2,1,0,width,height,len(payload),zlib.crc32(payload))
    result = header+struct.pack('<I',zlib.crc32(header))+payload
    legacy_size = 40+len(frames)*(12+width*height*2)
    return encode_animation(frames,durations,origin) if legacy_size <= len(result) else result
