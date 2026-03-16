"""Image analysis tool using Pillow."""

from __future__ import annotations

import math
import os
from collections import Counter

from PIL import Image, ExifTags


def _get_info(img: Image.Image, image_path: str) -> dict:
    """Basic image information."""
    return {
        "width": img.width,
        "height": img.height,
        "format": img.format,
        "mode": img.mode,
        "file_size_bytes": os.path.getsize(image_path),
        "megapixels": round(img.width * img.height / 1_000_000, 2),
    }


def _get_colors(img: Image.Image, num_colors: int = 10) -> list[dict]:
    """Extract dominant colours by quantising the image to a palette."""
    # Work on a small copy for speed
    small = img.copy()
    small.thumbnail((200, 200), Image.LANCZOS)
    small = small.convert("RGB")

    # Quantise to a palette
    quantised = small.quantize(colors=num_colors, method=Image.Quantize.MEDIANCUT)
    palette = quantised.getpalette()
    if not palette:
        return []

    # Count pixels per palette index
    pixel_counts = Counter(quantised.getdata())
    total_pixels = small.width * small.height

    colors = []
    for idx, count in pixel_counts.most_common(num_colors):
        r = palette[idx * 3]
        g = palette[idx * 3 + 1]
        b = palette[idx * 3 + 2]
        hex_color = f"#{r:02x}{g:02x}{b:02x}"
        colors.append({
            "rgb": [r, g, b],
            "hex": hex_color,
            "percentage": round(count / total_pixels * 100, 2),
        })

    return colors


def _get_histogram(img: Image.Image) -> dict:
    """Compute per-channel statistics from the image histogram."""
    img_rgb = img.convert("RGB")
    histogram = img_rgb.histogram()

    result = {}
    channel_names = ["red", "green", "blue"]

    for ch_idx, ch_name in enumerate(channel_names):
        # Each channel has 256 bins
        ch_hist = histogram[ch_idx * 256 : (ch_idx + 1) * 256]
        total_pixels = sum(ch_hist)
        if total_pixels == 0:
            result[ch_name] = {"mean": 0, "std": 0, "min": 0, "max": 0, "median": 0}
            continue

        # Mean
        mean = sum(i * count for i, count in enumerate(ch_hist)) / total_pixels

        # Standard deviation
        variance = sum(count * (i - mean) ** 2 for i, count in enumerate(ch_hist)) / total_pixels
        std = math.sqrt(variance)

        # Min / Max (first and last non-zero bin)
        min_val = 0
        for i, count in enumerate(ch_hist):
            if count > 0:
                min_val = i
                break

        max_val = 255
        for i in range(255, -1, -1):
            if ch_hist[i] > 0:
                max_val = i
                break

        # Median
        cumulative = 0
        median = 0
        half = total_pixels / 2
        for i, count in enumerate(ch_hist):
            cumulative += count
            if cumulative >= half:
                median = i
                break

        result[ch_name] = {
            "mean": round(mean, 2),
            "std": round(std, 2),
            "min": min_val,
            "max": max_val,
            "median": median,
        }

    return result


def _get_exif(img: Image.Image) -> dict:
    """Extract EXIF metadata if available."""
    exif_data = {}
    try:
        raw_exif = img.getexif()
        if not raw_exif:
            return {}
        for tag_id, value in raw_exif.items():
            tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
            # Convert non-serialisable types to strings
            if isinstance(value, bytes):
                try:
                    value = value.decode("utf-8", errors="replace")
                except Exception:
                    value = repr(value)
            elif not isinstance(value, (str, int, float, bool, type(None))):
                value = str(value)
            exif_data[tag_name] = value
    except Exception:
        pass
    return exif_data


_OPERATIONS_MAP = {
    "info": _get_info,
    "colors": _get_colors,
    "histogram": _get_histogram,
    "exif": _get_exif,
}

_DEFAULT_OPERATIONS = ["info", "colors", "histogram"]


def run(image_path: str, operations: list[str] | None = None) -> dict:
    """Analyse an image and return metadata, colours, and statistics.

    Parameters
    ----------
    image_path : str
        Path to the image file.
    operations : list[str] | None
        Operations to perform. Defaults to ``["info", "colors", "histogram"]``.
        Available: info, colors, histogram, exif.

    Returns
    -------
    dict with results keyed by operation name.
    """
    if not image_path:
        return {"status": "error", "message": "image_path is required"}
    if not os.path.isfile(image_path):
        return {"status": "error", "message": f"File not found: {image_path}"}

    if operations is None:
        operations = list(_DEFAULT_OPERATIONS)

    # Validate operations
    invalid = [op for op in operations if op not in _OPERATIONS_MAP]
    if invalid:
        return {
            "status": "error",
            "message": f"Unknown operations: {invalid}. Valid: {list(_OPERATIONS_MAP.keys())}",
        }

    try:
        img = Image.open(image_path)
    except Exception as exc:
        return {"status": "error", "message": f"Failed to open image: {exc}"}

    result: dict = {"status": "ok", "image_path": image_path}

    for op in operations:
        try:
            if op == "info":
                result["info"] = _get_info(img, image_path)
            elif op == "colors":
                num_colors = 10
                result["colors"] = _get_colors(img, num_colors=num_colors)
            elif op == "histogram":
                result["histogram"] = _get_histogram(img)
            elif op == "exif":
                result["exif"] = _get_exif(img)
        except Exception as exc:
            result[op] = {"error": str(exc)}

    return result
