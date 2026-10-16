from dataclasses import dataclass
import re


@dataclass(frozen=True)
class TextLayer:
    text: str = 'Привет :3'
    font: str = 'Segoe UI'
    size: int = 24
    color: str = '#ffffff'
    x: int = 12
    y: int = 12
    width: int = 296
    align: str = 'left'
    outline: int = 1
    outline_color: str = '#000000'
    background: bool = False
    background_color: str = '#000000'


def validate_layers(layers):
    if not isinstance(layers,(tuple,list)) or len(layers)>8:raise ValueError('Можно добавить до 8 текстовых слоёв.')
    for layer in layers:
        if not isinstance(layer,TextLayer):raise ValueError('Неверный текстовый слой.')
        if not isinstance(layer.text,str) or len(layer.text)>512 or not isinstance(layer.font,str) or not 1<=len(layer.font)<=128:
            raise ValueError('Текст слоя: до 512 символов; выбери установленный шрифт.')
        for key,low,high in (('size',8,96),('x',-640,640),('y',-640,640),('width',16,320),('outline',0,3)):
            value=getattr(layer,key)
            if type(value) is not int or not low<=value<=high:raise ValueError('Параметры текста вне допустимых границ.')
        for key in ('color','outline_color','background_color'):
            if not isinstance(getattr(layer,key),str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',getattr(layer,key)):
                raise ValueError('Неверный цвет текста.')
        if layer.align not in ('left','center','right') or type(layer.background) is not bool:raise ValueError('Неверное выравнивание или фон текста.')
    return tuple(layers)
