"""Анализ спутникового снимка: ищем тёмно-бурое пятно на воде (шлейф сброса).

Это простое правило по цвету пикселей, а не обученная модель:
  1. вода      — синий канал заметно больше красного;
  2. «грязь»   — тёмный пиксель, где красный больше синего;
  3. шлейф     — «грязь», рядом с которой много воды (так отсекаем тени на суше).
Если доля шлейфа на снимке >= PLUME_MIN_FRACTION — считаем, что есть сброс.
"""
import io

import numpy as np
from PIL import Image, UnidentifiedImageError
from scipy.ndimage import uniform_filter

from config import PLUME_MIN_FRACTION


class ImageError(Exception):
    pass


def analyze(raw: bytes) -> tuple[bytes, bool, float, float]:
    """Возвращает (jpeg-байты, есть_сброс, уверенность, доля_шлейфа)."""
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise ImageError("file is not a readable image (jpg/png)")

    small = np.asarray(img.resize((128, 128))).astype(float)
    r, g, b = small[..., 0], small[..., 1], small[..., 2]
    water = b > r + 15
    dirty = (r > b + 10) & ((r + g + b) / 3 < 75)
    water_nearby = uniform_filter(water.astype(float), size=41) > 0.3
    fraction = float((dirty & water_nearby).mean())

    detected = fraction >= PLUME_MIN_FRACTION
    confidence = round(min(0.99, 0.6 + 5 * fraction), 2)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)  # храним всегда как .jpg (контракт: /images/{id}.jpg)
    return out.getvalue(), detected, confidence, fraction
