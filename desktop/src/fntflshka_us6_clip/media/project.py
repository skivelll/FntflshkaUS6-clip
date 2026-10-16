"""Explicit local image projects; no USB identity or automatic asset cache."""
import io
import json
import os
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path
from PIL import Image
from .image import Placement

MAX_PROJECT=64*1024*1024


def save_project(path,source,placement):
    data=io.BytesIO();source.save(data,format='PNG')
    if data.tell()>MAX_PROJECT-65536:
        raise ValueError('Исходник слишком большой для проекта: максимум 64 MiB.')
    from .text_model import validate_layers
    validate_layers(placement.text_layers)
    values=asdict(placement);pixels=values.pop('paint_pixels')
    from .drawing import paint_image
    drawing=io.BytesIO()
    (paint_image(pixels,(placement.width,placement.height)) if pixels else Image.new('RGBA',(placement.width,placement.height))).save(drawing,format='PNG')
    metadata=json.dumps({'version':3,'placement':values},ensure_ascii=False).encode('utf-8')
    if len(metadata)>32768:raise ValueError('Слишком большие настройки проекта.')
    if data.tell()+drawing.tell()+len(metadata)+4096>MAX_PROJECT:raise ValueError('Проект с рисунком превышает 64 MiB.')
    path=Path(path)
    fd,temp=tempfile.mkstemp(prefix='.clip-image-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED) as archive:
                archive.writestr('source.png',data.getvalue())
                archive.writestr('settings.json',metadata)
                archive.writestr('drawing.png',drawing.getvalue())
            stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def load_project(path):
    path=Path(path)
    if path.stat().st_size>MAX_PROJECT:raise ValueError('Проект превышает 64 MiB.')
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist()
        names={e.filename for e in entries}
        if len(names)!=len(entries) or names not in ({'source.png','settings.json'},{'source.png','settings.json','drawing.png'}):
            raise ValueError('Неверная структура проекта изображения.')
        if any(e.flag_bits&1 or e.file_size>MAX_PROJECT for e in entries) or sum(e.file_size for e in entries)>MAX_PROJECT:
            raise ValueError('Проект превышает безопасный размер.')
        if archive.getinfo('settings.json').file_size>32768:raise ValueError('Слишком большие настройки проекта.')
        metadata=json.loads(archive.read('settings.json'))
        if not isinstance(metadata,dict) or set(metadata)!={'version','placement'} or type(metadata.get('version')) is not int or metadata.get('version') not in (1,2,3):raise ValueError('Неизвестная версия проекта.')
        if ('drawing.png' in names)!=(metadata['version']==3):raise ValueError('Состав проекта не совпадает с версией.')
        values=metadata.get('placement')
        fields=set(asdict(Placement()))-{'paint_pixels'};text_fields={'text_layers','text_motion','text_speed','text_pause'}
        if not isinstance(values,dict) or not fields-text_fields<=set(values)<=fields:raise ValueError('Неверные настройки проекта.')
        from .text_model import TextLayer,validate_layers
        try:values['text_layers']=validate_layers(tuple(TextLayer(**layer) for layer in values.get('text_layers',())))
        except (TypeError,KeyError) as error:raise ValueError('Неверные текстовые слои проекта.') from error
        p=Placement(**values)
        if p.text_motion not in ('static','left','right') or type(p.text_speed) is not int or not 10<=p.text_speed<=120 or type(p.text_pause) is not int or not 0<=p.text_pause<=2000:
            raise ValueError('Неверные настройки движения текста.')
        for name in ('width','height','scale','x','y','sharpness'):
            if type(getattr(p,name)) is not int:raise ValueError('Неверное число в настройках проекта.')
        if (not 1<=p.width<=320 or not 1<=p.height<=172 or not 10<=p.scale<=400
            or not -640<=p.x<=640 or not -640<=p.y<=640 or not 0<=p.sharpness<=100
            or p.mode not in ('fit','fill') or p.resampling not in ('lanczos','bicubic')
            or type(p.pixel_art) is not bool or type(p.dithering) is not bool
            or not isinstance(p.background,str) or len(p.background)!=7 or p.background[0]!='#'):
            raise ValueError('Настройки проекта вне допустимых границ.')
        try:int(p.background[1:],16)
        except ValueError:raise ValueError('Неверный цвет фона проекта.') from None
        with Image.open(io.BytesIO(archive.read('source.png'))) as image:
            if image.format!='PNG' or image.width*image.height>40_000_000 or getattr(image,'is_animated',False):
                raise ValueError('Нужен статичный PNG до 40 мегапикселей.')
            source=image.convert('RGBA')
        if metadata['version']==3:
            if archive.getinfo('drawing.png').file_size>1024*1024:raise ValueError('Слой рисунка слишком большой.')
            with Image.open(io.BytesIO(archive.read('drawing.png'))) as drawing:
                if drawing.format!='PNG' or drawing.size!=(p.width,p.height) or getattr(drawing,'is_animated',False):raise ValueError('Неверный размер или формат слоя рисунка.')
                rgba=drawing.convert('RGBA')
                from dataclasses import replace
                p=replace(p,paint_pixels=rgba.tobytes() if rgba.getbbox() else b'')
    return source,p
