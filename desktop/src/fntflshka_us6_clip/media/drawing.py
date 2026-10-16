"""Bounded independent LCD paint layer; no firmware or Qt dependency."""
from PIL import Image,ImageDraw,ImageColor


def paint_image(pixels,target):
    if not isinstance(pixels,bytes) or len(pixels)!=target[0]*target[1]*4:
        raise ValueError('Размер слоя рисунка не совпадает с экраном. Открой проект на подходящем устройстве или очисти рисунок.')
    return Image.frombytes('RGBA',target,pixels)


def overlay(image,pixels):
    if not pixels:return image
    result=image.convert('RGBA');result.alpha_composite(paint_image(pixels,image.size))
    return result.convert('RGB')


def prepare_paint(base,placement):
    from .image import PreparedImage
    from ..core.asset import encode_asset
    from .rgb565 import decode_rgb565
    asset=encode_asset(overlay(base.preview,placement.paint_pixels))
    before=None
    if base.before is not None:
        before=decode_rgb565(encode_asset(overlay(base.before,placement.paint_pixels))[24:],base.preview.size)
    return PreparedImage(asset,decode_rgb565(asset[24:],base.preview.size),before=before)


def flood(pixels,target,point,color):
    image=paint_image(pixels,target)
    if not 0<=point[0]<target[0] or not 0<=point[1]<target[1]:raise ValueError('Точка заливки вне рисунка.')
    ImageDraw.floodfill(image,point,ImageColor.getcolor(color,'RGBA'),thresh=0)
    return image.tobytes()


class DrawingDocument:
    def __init__(self,target=(320,172),pixels=b''):
        if not 1<=target[0]<=320 or not 1<=target[1]<=172:raise ValueError('Неверный размер холста.')
        self.target=target;self.image=paint_image(pixels,target) if pixels else Image.new('RGBA',target)
        self.history=[self.image.tobytes()];self.future=[];self.revision=0;self.last=None

    def pixels(self):return self.image.tobytes() if self.image.getbbox() else b''

    def begin(self,point,color,size,erase=False):
        self.last=point;self.color=(0,0,0,0) if erase else ImageColor.getcolor(color,'RGBA');self.size=size
        self.line(point)

    def line(self,point):
        if self.last is None:return
        size=self.size
        if not 1<=size<=16:raise ValueError('Размер кисти: 1–16 пикселей.')
        x=max(-size,min(self.target[0]+size,int(point[0])));y=max(-size,min(self.target[1]+size,int(point[1])))
        point=(x,y);draw=ImageDraw.Draw(self.image)
        draw.line((self.last,point),fill=self.color,width=size)
        for px,py in (self.last,point):
            left,top=px-size//2,py-size//2
            draw.rectangle((left,top,left+size-1,top+size-1),fill=self.color)
        self.last=point;self.revision+=1

    def end(self):self.last=None;self.commit()

    def commit(self):
        data=self.image.tobytes()
        if data!=self.history[-1]:self.history.append(data);self.history=self.history[-51:];self.future=[]

    def replace(self,pixels):
        self.image=paint_image(pixels,self.target);self.revision+=1;self.commit()

    def clear(self):self.replace(bytes(self.target[0]*self.target[1]*4))

    def undo(self):
        if len(self.history)<2:return
        self.future.append(self.history.pop());self.image=paint_image(self.history[-1],self.target);self.revision+=1

    def redo(self):
        if not self.future:return
        data=self.future.pop();self.history.append(data);self.image=paint_image(data,self.target);self.revision+=1
