"""Generate favicon-style icons from text (1-2 letter abbreviation) using Pillow."""

from __future__ import annotations

import os
from pathlib import Path


def run(
    text: str,
    output_dir: str = "",
    background_color: str = "#3b82f6",
    text_color: str = "#ffffff",
    sizes: list[int] | None = None,
) -> dict:
    """Generate favicon-style icons with text on a coloured background.

    Args:
        text: Short text to render (1-2 characters recommended).
        output_dir: Directory to save icons. Defaults to current directory.
        background_color: Hex background colour (e.g. "#3b82f6").
        text_color: Hex text colour (e.g. "#ffffff").
        sizes: List of icon sizes in pixels. Defaults to [16, 32, 48, 64, 128, 256, 512].

    Returns:
        dict with icons (list of {path, size}) and output_dir.
    """
    from PIL import Image, ImageDraw, ImageFont

    if sizes is None:
        sizes = [16, 32, 48, 64, 128, 256, 512]

    if not output_dir:
        output_dir = "."

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Truncate text to 2 characters for icon display
    display_text = text[:2].upper()

    bg = _hex_to_rgb(background_color)
    fg = _hex_to_rgb(text_color)

    icons: list[dict] = []

    for size in sorted(sizes):
        img = Image.new("RGBA", (size, size), bg + (255,))
        draw = ImageDraw.Draw(img)

        # Try to load a good font at the right size; fall back to default
        font = _get_font(size, len(display_text))

        # Calculate text position for centering using textbbox
        bbox = draw.textbbox((0, 0), display_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        x = (size - text_width) / 2 - bbox[0]
        y = (size - text_height) / 2 - bbox[1]

        draw.text((x, y), display_text, fill=fg + (255,), font=font)

        filename = f"icon-{size}x{size}.png"
        filepath = out / filename
        img.save(str(filepath), "PNG")
        icons.append({"path": str(filepath.resolve()), "size": size})

    return {
        "icons": icons,
        "output_dir": str(out.resolve()),
    }


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert a hex colour string to an RGB tuple."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _get_font(size: int, text_len: int):
    """Get the best available font at an appropriate size for the icon."""
    from PIL import ImageFont

    # Font size should be proportional to icon size
    # For 1 char, use ~70% of icon size; for 2 chars, use ~50%
    ratio = 0.70 if text_len <= 1 else 0.50
    font_size = max(8, int(size * ratio))

    # Try common system font paths
    font_paths = [
        # Windows
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        # macOS
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSText.ttf",
        "/Library/Fonts/Arial.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]

    for path in font_paths:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, font_size)
            except (OSError, IOError):
                continue

    # Fallback to Pillow's built-in default font (bitmap, not scalable)
    try:
        return ImageFont.truetype("arial", font_size)
    except (OSError, IOError):
        return ImageFont.load_default()
