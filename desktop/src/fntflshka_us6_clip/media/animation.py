from dataclasses import dataclass, replace
from pathlib import Path
import warnings
from PIL import Image
from ..core.asset import parse_asset, first_frame
from ..core.animation import encode_animation, minimum_duration, MAX_FILE
from .image import Placement, prepare_image as prepare_static, PreparedImage


def prepare_image(source, placement):
    # Static quality settings must never change existing GIF encoding/timing.
    return prepare_static(source,replace(placement,resampling='bicubic',sharpness=0,dithering=False,text_layers=(),text_motion='static'),comparison=False)
from .rgb565 import decode_rgb565
from collections import OrderedDict
from ..core.capacity import MAX_FRAMES


@dataclass(frozen=True)
class GifSource:
    path: Path
    mode: str = "compact"
    first: int = 0
    last: int = 0
    step: int = 1
    optimize: bool = False


def load_gif(path, request=None):
    if Path(path).stat().st_size > 32 * 1024 * 1024:
        raise ValueError("GIF больше 32 MiB.")
    frames, durations = [], []
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            if image.format != "GIF" or image.width * image.height > 4_000_000:
                raise ValueError("Нужен GIF до 4 мегапикселей.")
            count = image.n_frames
            if not 2 <= count <= MAX_FRAMES:
                raise ValueError(f"В исходном GIF {count} кадров; поддерживается от 2 до {MAX_FRAMES}.")
            first = request.first if request else 0
            end = min(request.last or count, count) if request else count
            step = request.step if request else 1
            if not 0 <= first < end or not 1 <= step <= MAX_FRAMES:
                raise ValueError("Неверный диапазон кадров GIF.")
            selected = len(range(first, end, step))
            if image.width * image.height * selected > 32_000_000:
                raise ValueError(f"Выбрано {selected} кадров исходного размера {image.width}×{image.height}: "
                                 "слишком много памяти для обработки (предел 32 MP). Сократи диапазон или увеличь шаг.")
            if request and not request.optimize:
                width, height = {"compact": (128,128), "small": (96,96), "full": (320,172)}[request.mode]
                maximum = (MAX_FILE - 40) // (12 + width * height * 2)
                if not 2 <= selected <= maximum:
                    raise ValueError(f"Выбрано {selected} кадров; профиль {width}×{height} допускает 2–{maximum}. Сократи диапазон или увеличь шаг.")
            for i in range(first, end):
                image.seek(i)  # Pillow applies GIF disposal/transparency composition.
                duration = max(10, int(image.info.get("duration", 100)))
                if (i - first) % step == 0:
                    frames.append(image.convert("RGBA").copy())
                    durations.append(duration)
                else:
                    durations[-1] += duration
    return tuple(frames), tuple(durations)


class AnimationPreview:
    """Decoded native-size pixels; keep only two composed LCD canvases in RAM.

    RGB565 conversion happens in the image/library worker. UI access only pastes
    already decoded pixels onto a bounded 320x172 canvas.
    """
    def __init__(self, frames, target, origin):
        self.frames, self.target, self.origin = tuple(frames), target, origin
        self.canvases = OrderedDict()

    def __len__(self):
        return len(self.frames)

    def __getitem__(self, index):
        if index < 0:
            index += len(self.frames)
        pixels, duration = self.frames[index]
        if index not in self.canvases:
            canvas = Image.new("RGB", self.target, "black")
            canvas.paste(pixels, self.origin)
            self.canvases[index] = canvas
            while len(self.canvases) > 2:
                self.canvases.popitem(last=False)
        return self.canvases[index], duration


def preview_frames(asset, target=(320, 172)):
    info = parse_asset(asset)
    frames = info.get("frames", ((24, 1000, 0),))
    x = info.get("x", (target[0] - info["width"]) // 2)
    y = info.get("y", (target[1] - info["height"]) // 2)
    result = []
    if info.get('encoding') == 1:
        from ..core.compression import decoded_frames
        result = [(decode_rgb565(raw,(info['width'],info['height'])),ms) for raw,ms in decoded_frames(asset,info)]
        return AnimationPreview(result,target,(x,y))
    for offset, duration, _ in frames:
        pixels = decode_rgb565(asset[offset:offset + info["width"] * info["height"] * 2], (info["width"], info["height"]))
        result.append((pixels, duration))
    return AnimationPreview(result, target, (x, y))


def prepare_gif(loaded, request, placement, *, measure_only=False):
    frames, durations = loaded
    end = min(request.last or len(frames), len(frames))
    if not 0 <= request.first < end or not 1 <= request.step <= MAX_FRAMES:
        raise ValueError("Неверный диапазон кадров GIF.")
    sizes = {"compact": (128, 128), "small": (96, 96), "full": (320, 172)}
    if request.mode not in sizes:
        raise ValueError("Неизвестный профиль GIF.")
    width, height = sizes[request.mode]
    if width > placement.width or height > placement.height:
        raise ValueError("Профиль не помещается на экран.")
    count = len(range(request.first, end, request.step))
    if request.optimize and count*width*height > 32_000_000:
        raise ValueError('Выбранные кадры превышают бюджет preview 32 MP. Сократи диапазон или увеличь шаг.')
    predicted = 40 + count * (12 + width * height * 2)
    maximum = (MAX_FILE - 40) // (12 + width * height * 2)
    if count < 2 or (not request.optimize and count > maximum):
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
    from ..core.compression import encode_optimized
    if measure_only:
        if not request.optimize:
            raise ValueError('Measurement requires the optimized encoder.')
        return encode_optimized(output,times,(x,y),measure_only=True)
    asset = (encode_optimized if request.optimize else encode_animation)(output, times, (x, y))
    previews = preview_frames(asset, (placement.width, placement.height))
    summary = f"GIF {width}×{height} · {len(output)} кадров · {sum(times)/1000:g} с · {len(asset):,} Б. Темп может быть медленнее исходного."
    if request.optimize:
        summary += f" Без сжатия {predicted:,} Б; экономия {100*(1-len(asset)/predicted):.1f}%."
    return PreparedImage(asset, previews[0][0], previews, summary)


def prepare_cached(source, placement):
    info = parse_asset(source.asset)
    previews = preview_frames(source.asset,(info['width'],info['height']))
    # Native pixels, not canvases with LCD origin offsets.
    loaded = (tuple(p for p,_ in previews.frames),tuple(d for _,d in previews.frames))
    # Existing animation keeps its exact profile and durations; edits affect all frames.
    full = info['width'] == 320 and info['height'] == 172
    output = [prepare_image(frame, replace(placement, width=info['width'], height=info['height'],
                                          x=placement.x if full else 0, y=placement.y if full else 0)).preview for frame in loaded[0]]
    origin = (max(0, min(320-info['width'], info['x']+placement.x)),
              max(0, min(172-info['height'], info['y']+placement.y)))
    from ..core.compression import encode_optimized
    asset = (encode_optimized if info.get('encoding') else encode_animation)(output, loaded[1], origin)
    previews = preview_frames(asset, source.target)
    return PreparedImage(asset, previews[0][0], previews, f"Анимация · {len(output)} кадров · {sum(loaded[1])/1000:g} с")
