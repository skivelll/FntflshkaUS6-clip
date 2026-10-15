from dataclasses import dataclass, replace
from pathlib import Path
import warnings
from PIL import Image
from ..core.asset import parse_asset, first_frame
from ..core.animation import encode_animation, minimum_duration, MAX_FILE
from .image import Placement, prepare_image, PreparedImage
from .rgb565 import decode_rgb565


@dataclass(frozen=True)
class GifSource:
    path: Path
    mode: str = "compact"
    first: int = 0
    last: int = 0
    step: int = 1


def load_gif(path):
    if Path(path).stat().st_size > 32 * 1024 * 1024:
        raise ValueError("GIF больше 32 MiB.")
    frames, durations = [], []
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            if image.format != "GIF" or image.width * image.height > 4_000_000:
                raise ValueError("Нужен GIF до 4 мегапикселей.")
            count = image.n_frames
            if not 2 <= count <= 300 or image.width * image.height * count > 32_000_000:
                raise ValueError("GIF должен содержать 2–300 кадров и не больше 32 MP суммарно.")
            for i in range(count):
                image.seek(i)  # Pillow applies GIF disposal/transparency composition.
                frames.append(image.convert("RGBA").copy())
                durations.append(max(10, int(image.info.get("duration", 100))))
    return tuple(frames), tuple(durations)


def preview_frames(asset, target=(320, 172)):
    info = parse_asset(asset)
    frames = info.get("frames", ((24, 1000, 0),))
    x = info.get("x", (target[0] - info["width"]) // 2)
    y = info.get("y", (target[1] - info["height"]) // 2)
    result = []
    for offset, duration, _ in frames:
        pixels = decode_rgb565(asset[offset:offset + info["width"] * info["height"] * 2], (info["width"], info["height"]))
        canvas = Image.new("RGB", target, "black")
        canvas.paste(pixels, (x, y))
        result.append((canvas, duration))
    return tuple(result)


def prepare_gif(loaded, request, placement):
    frames, durations = loaded
    end = min(request.last or len(frames), len(frames))
    if not 0 <= request.first < end or not 1 <= request.step <= 20:
        raise ValueError("Неверный диапазон кадров GIF.")
    sizes = {"compact": (128, 128), "small": (96, 96), "full": (320, 172)}
    if request.mode not in sizes:
        raise ValueError("Неизвестный профиль GIF.")
    width, height = sizes[request.mode]
    if width > placement.width or height > placement.height:
        raise ValueError("Профиль не помещается на экран.")
    count = len(range(request.first, end, request.step))
    predicted = 40 + count * (12 + width * height * 2)
    maximum = min(128, (MAX_FILE - 40) // (12 + width * height * 2))
    if not 2 <= count <= maximum:
        raise ValueError(f"Выбрано {count} кадров ({predicted:,} Б). Для {width}×{height} нужно 2–{maximum} кадров. Открой «Настройки GIF…», сократи диапазон или увеличь шаг.")
    output, times = [], []
    for i in range(request.first, end, request.step):
        p = replace(placement, width=width, height=height,
                    x=placement.x if request.mode == "full" else 0,
                    y=placement.y if request.mode == "full" else 0)
        output.append(prepare_image(frames[i], p).preview)
        times.append(max(minimum_duration(width, height), sum(durations[i:min(i + request.step, end)])))
    x = max(0, min(320 - width, (320 - width) // 2 + (placement.x if request.mode != "full" else 0)))
    y = max(0, min(172 - height, (172 - height) // 2 + (placement.y if request.mode != "full" else 0)))
    asset = encode_animation(output, times, (x, y))
    previews = preview_frames(asset, (placement.width, placement.height))
    summary = f"GIF {width}×{height} · {len(output)} кадров · {sum(times)/1000:g} с · {len(asset):,} Б. Темп может быть медленнее исходного."
    return PreparedImage(asset, previews[0][0], previews, summary)


def prepare_cached(source, placement):
    info = parse_asset(source.asset)
    loaded = (tuple(decode_rgb565(source.asset[o:o + info['frame_bytes']], (info['width'], info['height']))
                    for o, _, _ in info['frames']), tuple(d for _, d, _ in info['frames']))
    # Existing animation keeps its exact profile and durations; edits affect all frames.
    full = info['width'] == 320 and info['height'] == 172
    output = [prepare_image(frame, replace(placement, width=info['width'], height=info['height'],
                                          x=placement.x if full else 0, y=placement.y if full else 0)).preview for frame in loaded[0]]
    origin = (max(0, min(320-info['width'], info['x']+placement.x)),
              max(0, min(172-info['height'], info['y']+placement.y)))
    asset = encode_animation(output, loaded[1], origin)
    previews = preview_frames(asset, source.target)
    return PreparedImage(asset, previews[0][0], previews, f"Анимация · {len(output)} кадров · {sum(loaded[1])/1000:g} с")
