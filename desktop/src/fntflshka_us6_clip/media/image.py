from dataclasses import dataclass
from pathlib import Path
import warnings
from PIL import Image, ImageOps, ImageFilter
from dataclasses import replace
import math
from ..core.asset import encode_asset, parse_asset
from .rgb565 import decode_rgb565


@dataclass(frozen=True)
class Placement:
    width: int = 320
    height: int = 172
    mode: str = "fit"
    scale: int = 100
    x: int = 0
    y: int = 0
    pixel_art: bool = False
    background: str = "#000000"
    resampling: str = "lanczos"
    sharpness: int = 25
    dithering: bool = False
    text_layers: tuple = ()
    text_motion: str = 'static'
    text_speed: int = 40
    text_pause: int = 0


@dataclass(frozen=True)
class PreparedImage:
    asset: bytes
    preview: Image.Image
    frames: tuple = ()
    summary: str = ""
    before: Image.Image | None = None
    animation_start: int = 0
    preview_only: bool = False


@dataclass(frozen=True)
class EditorRenderRequest:
    placement: Placement
    preview_only: bool = False


@dataclass(frozen=True)
class SceneSource:
    """Immutable verified device pixels; no temporary PNG or Qt objects."""
    asset: bytes
    target: tuple[int, int]


class BlankSource(SceneSource):
    """Transparent local source: the selected background remains editable."""


def scene_canvas(source: SceneSource):
    if isinstance(source,BlankSource):return Image.new('RGBA',source.target)
    info = parse_asset(source.asset)
    if info.get("type") == 2:
        from .animation import preview_frames
        return preview_frames(source.asset, source.target)[0][0].convert("RGBA")
    pixels = decode_rgb565(source.asset[24:], (info["width"], info["height"]))
    if not 1 <= source.target[0] <= 320 or not 1 <= source.target[1] <= 172 or pixels.width > source.target[0] or pixels.height > source.target[1]:
        raise ValueError("Сохранённая сцена не подходит для этого экрана.")
    canvas = Image.new("RGBA", source.target, "black")
    canvas.paste(pixels, ((canvas.width - pixels.width) // 2, (canvas.height - pixels.height) // 2))
    return canvas


def load_image(path: Path):
    if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        raise ValueError("Выбери PNG, JPG или WEBP. Анимации пока не поддерживаются.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as source:
            if source.format not in ("PNG", "JPEG", "WEBP") or getattr(source, "is_animated", False):
                raise ValueError("Нужна статичная картинка PNG, JPG или WEBP.")
            if source.width * source.height > 40_000_000:
                raise ValueError("Картинка слишком большая. Уменьши её до 40 мегапикселей.")
            return ImageOps.exif_transpose(source).convert("RGBA")


def prepare_image(source, placement: Placement, *, comparison=True):
    if placement.text_layers:
        from .text import prepare_text
        return prepare_text(source,placement)
    width, height = placement.width, placement.height
    if not 1 <= width <= 320 or not 1 <= height <= 172 or not 10 <= placement.scale <= 400:
        raise ValueError("Неподдерживаемый размер экрана или масштаб.")
    if placement.mode not in ("fit", "fill"):
        raise ValueError("Неизвестный способ размещения.")
    if placement.resampling not in ('lanczos','bicubic') or not 0 <= placement.sharpness <= 100:
        raise ValueError('Неизвестный алгоритм или уровень резкости.')
    ratios = (width / source.width, height / source.height)
    factor = (min(ratios) if placement.mode == "fit" else max(ratios)) * placement.scale / 100
    left = (width - source.width * factor) / 2 + placement.x
    top = (height - source.height * factor) / 2 + placement.y
    rgba=source if source.mode=='RGBA' else source.convert('RGBA')
    # Inverse affine rendering avoids allocating enormous resized panoramas.
    if not placement.pixel_art and placement.resampling == 'lanczos':
        # Resize only the visible source rectangle. Even a very wide panorama
        # never allocates a scaled panorama or a canvas larger than the LCD.
        layer = Image.new('RGBA',(width,height))
        x0,y0=max(0,math.ceil(left)),max(0,math.ceil(top))
        x1,y1=min(width,math.floor(left+source.width*factor)),min(height,math.floor(top+source.height*factor))
        if x1>x0 and y1>y0:
            box=((x0-left)/factor,(y0-top)/factor,(x1-left)/factor,(y1-top)/factor)
            patch=rgba.resize((x1-x0,y1-y0),Image.Resampling.LANCZOS,box=box)
            layer.paste(patch,(x0,y0))
    else:
        layer = rgba.transform(
        (width, height), Image.Transform.AFFINE,
        (1 / factor, 0, -left / factor, 0, 1 / factor, -top / factor),
        resample=Image.Resampling.NEAREST if placement.pixel_art else Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
        )
    canvas = Image.new("RGBA", (width, height), placement.background)
    canvas.alpha_composite(layer)
    rgb=canvas.convert('RGB')
    if not placement.pixel_art and placement.sharpness:
        rgb=rgb.filter(ImageFilter.UnsharpMask(radius=.65,percent=placement.sharpness,threshold=2))
    if not placement.pixel_art and placement.dithering:
        rgb=dither_rgb565(rgb)
    asset = encode_asset(rgb)
    before=None
    if comparison:
        before=prepare_image(source,replace(placement,resampling='bicubic',sharpness=0,dithering=False),comparison=False).preview
    return PreparedImage(asset, decode_rgb565(asset[24:], (width, height)),before=before)


def dither_rgb565(image):
    """Deterministic Floyd–Steinberg in final LCD coordinates, per 5/6/5 channel."""
    width,height=image.size
    pixels=image.tobytes();out=bytearray(width*height*3)
    current=[[0.0]*3 for _ in range(width+2)]
    for y in range(height):
        following=[[0.0]*3 for _ in range(width+2)]
        for x in range(width):
            for c,bits in enumerate((5,6,5)):
                value=max(0,min(255,pixels[(y*width+x)*3+c]+current[x+1][c]))
                level=round(value*((1<<bits)-1)/255)
                decoded=(level<< (8-bits)) | (level >> (2*bits-8))
                out[(y*width+x)*3+c]=level << (8-bits)
                error=value-decoded
                current[x+2][c]+=error*7/16
                following[x][c]+=error*3/16
                following[x+1][c]+=error*5/16
                following[x+2][c]+=error/16
        current=following
    return Image.frombytes('RGB',(width,height),bytes(out))
