from dataclasses import dataclass
from pathlib import Path
import warnings
from PIL import Image, ImageOps
from ..core.asset import encode_asset
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


@dataclass(frozen=True)
class PreparedImage:
    asset: bytes
    preview: Image.Image


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


def prepare_image(source, placement: Placement):
    width, height = placement.width, placement.height
    if not 1 <= width <= 320 or not 1 <= height <= 172 or not 10 <= placement.scale <= 400:
        raise ValueError("Неподдерживаемый размер экрана или масштаб.")
    if placement.mode not in ("fit", "fill"):
        raise ValueError("Неизвестный способ размещения.")
    ratios = (width / source.width, height / source.height)
    factor = (min(ratios) if placement.mode == "fit" else max(ratios)) * placement.scale / 100
    left = (width - source.width * factor) / 2 + placement.x
    top = (height - source.height * factor) / 2 + placement.y
    # Inverse affine rendering avoids allocating enormous resized panoramas.
    layer = source.convert("RGBA").transform(
        (width, height), Image.Transform.AFFINE,
        (1 / factor, 0, -left / factor, 0, 1 / factor, -top / factor),
        resample=Image.Resampling.NEAREST if placement.pixel_art else Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )
    canvas = Image.new("RGBA", (width, height), placement.background)
    canvas.alpha_composite(layer)
    asset = encode_asset(canvas.convert("RGB"))
    return PreparedImage(asset, decode_rgb565(asset[24:], (width, height)))
