"""Desktop rasterized text, no firmware fonts or new asset format."""
import math
from dataclasses import replace
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QPainter,QPainterPath,QFont,QFontMetrics,QColor,QPen,QFontDatabase
from .text_model import validate_layers


def layer_image(layer):
    if layer.font not in QFontDatabase.families():
        raise ValueError(f'Шрифт «{layer.font}» не установлен. Выбери другой шрифт.')
    font=QFont(layer.font);font.setPixelSize(layer.size)
    metrics=QFontMetrics(font);lines=[]
    for paragraph in layer.text.split('\n'):
        line=''
        for char in paragraph:
            if line and metrics.horizontalAdvance(line+char)>layer.width:
                lines.append(line);line=''
            line+=char
        lines.append(line)
    # The LCD is the crop boundary; never allocate an unbounded text surface.
    border=layer.outline+2
    width=layer.width+border*2;height=min(172,max(1,len(lines)*metrics.height()+border*2))
    image=QImage(width,height,QImage.Format_RGBA8888);image.fill(Qt.transparent)
    painter=QPainter(image);painter.setRenderHint(QPainter.Antialiasing)
    if layer.background:painter.fillRect(image.rect(),QColor(layer.background_color))
    path=QPainterPath()
    for i,line in enumerate(lines):
        y=border+metrics.ascent()+i*metrics.height()
        if y-metrics.ascent()>=height:break
        length=metrics.horizontalAdvance(line)
        x=border+(0 if layer.align=='left' else (layer.width-length)//2 if layer.align=='center' else layer.width-length)
        path.addText(x,y,font,line)
    if layer.outline:
        pen=QPen(QColor(layer.outline_color),layer.outline*2,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin)
        painter.strokePath(path,pen)
    painter.fillPath(path,QColor(layer.color));painter.end()
    return Image.frombytes('RGBA',(width,height),bytes(image.constBits()),'raw','RGBA',image.bytesPerLine()),border


def prepared_layers(layers):
    validate_layers(layers)
    return tuple((layer,*layer_image(layer)) for layer in layers)


def composite(base,prepared,dx=0):
    result=base.convert('RGBA')
    for layer,image,border in prepared:result.alpha_composite(image,(layer.x-border+dx,layer.y-border))
    return result.convert('RGB')


def prepare_text(source,placement,base=None):
    from .image import prepare_image,PreparedImage
    from ..core.asset import encode_asset
    from .rgb565 import decode_rgb565
    layers=prepared_layers(placement.text_layers)
    if base is None:base=prepare_image(source,replace(placement,text_layers=(),text_motion='static'))
    if placement.text_motion=='static' or not layers:
        asset=encode_asset(composite(base.preview,layers))
        before=composite(base.before,layers) if base.before else None
        if before:before=decode_rgb565(encode_asset(before)[24:],base.preview.size)
        return PreparedImage(asset,decode_rgb565(asset[24:],base.preview.size),before=before)
    from ..core.animation import minimum_duration,MAX_FILE
    from ..core.compression import encode_optimized
    from .animation import preview_frames
    if placement.text_motion not in ('left','right') or type(placement.text_speed) is not int or not 10<=placement.text_speed<=120 or type(placement.text_pause) is not int or not 0<=placement.text_pause<=2000:
        raise ValueError('Скорость текста: 10–120 px/s, пауза: 0–2000 ms.')
    visible=[(layer,image,border,image.getbbox()) for layer,image,border in layers if image.getbbox()]
    if not visible:return base
    left=min(layer.x-border+box[0] for layer,image,border,box in visible)
    right=max(layer.x-border+box[2] for layer,image,border,box in visible)
    start=placement.width-left;end=-right
    if placement.text_motion=='right':start,end=end,start
    ms=minimum_duration(placement.width,placement.height)
    count=max(2,math.ceil(abs(end-start)*1000/placement.text_speed/ms)+1)
    if count*placement.width*placement.height>32_000_000:
        raise ValueError('Цикл текста слишком длинный для preview 32 MP. Увеличь скорость или сократи расстояние между слоями.')
    frames=[composite(base.preview,layers,round(start+(end-start)*i/(count-1))) for i in range(count)]
    durations=[ms]*count
    durations[0]+=placement.text_pause
    estimate=encode_optimized(frames,durations,measure_only=True)
    if estimate.file_size>MAX_FILE:
        raise ValueError(f'Бегущий текст занимает {estimate.file_size:,} Б: превышение на {estimate.file_size-MAX_FILE:,} Б. Увеличь скорость, уменьши текст или упрости фон.')
    asset=encode_optimized(frames,durations)
    previews=preview_frames(asset,(placement.width,placement.height))
    start_index=min(range(count),key=lambda i:abs(start+(end-start)*i/(count-1)))
    return PreparedImage(asset,previews[0][0],previews,
        f'Бегущий текст · {count} кадров · {sum(durations)/1000:g} с · {len(asset):,} Б · до {1000/ms:.1f} кадр/с. Цикл через кадр без текста; темп на LCD может быть ниже.',animation_start=start_index)
