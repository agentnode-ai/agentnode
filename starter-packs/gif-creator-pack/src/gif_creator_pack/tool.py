"""Animated GIF creator using Pillow."""

from __future__ import annotations

import os
import textwrap

from PIL import Image, ImageDraw, ImageFont


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert a hex colour string to an RGB tuple."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    return (
        int(hex_color[0:2], 16),
        int(hex_color[2:4], 16),
        int(hex_color[4:6], 16),
    )


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try to load a TrueType font; fall back to the default bitmap font."""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    ]
    for fp in font_paths:
        if os.path.isfile(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


# Palette of background colours for text frames
_PALETTE = [
    "#264653", "#2a9d8f", "#e9c46a", "#f4a261", "#e76f51",
    "#606c38", "#283618", "#bc6c25", "#0077b6", "#023e8a",
    "#d62828", "#003049", "#9b2226", "#005f73", "#94d2bd",
]


def _render_text_frame(
    text: str,
    width: int,
    height: int,
    bg_color: str,
    text_color: str = "#FFFFFF",
) -> Image.Image:
    """Render a text string centred on a coloured background."""
    bg_rgb = _hex_to_rgb(bg_color)
    txt_rgb = _hex_to_rgb(text_color)
    img = Image.new("RGB", (width, height), bg_rgb)
    draw = ImageDraw.Draw(img)

    font_size = max(16, min(width, height) // 10)
    font = _get_font(font_size)

    max_chars = max(10, int(width * 0.8 / (font_size * 0.55)))
    wrapped = textwrap.fill(text, width=max_chars)
    lines = wrapped.split("\n")
    line_height = font_size + 6
    total_height = line_height * len(lines)
    y = max(0, (height - total_height) // 2)

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = max(0, (width - tw) // 2)
        draw.text((x, y), line, fill=txt_rgb, font=font)
        y += line_height

    return img


def run(
    frames: list[str] | None = None,
    text_frames: list[str] | None = None,
    output_path: str = "",
    duration: int = 500,
    width: int = 400,
    height: int = 300,
) -> dict:
    """Create an animated GIF from image files or text strings.

    Parameters
    ----------
    frames : list[str] | None
        List of image file paths to combine into a GIF.
    text_frames : list[str] | None
        List of text strings to render as frames (coloured backgrounds).
    output_path : str
        Destination file path. Auto-generated if empty.
    duration : int
        Duration per frame in milliseconds (default 500).
    width, height : int
        Frame dimensions in pixels (used for text_frames and to resize
        image frames for consistency).

    Returns
    -------
    dict with ``output_path``, ``frame_count``, ``duration_ms``.
    """
    if not frames and not text_frames:
        return {"status": "error", "message": "Provide either frames (image paths) or text_frames (text list)"}

    pil_frames: list[Image.Image] = []

    if frames:
        for fp in frames:
            if not os.path.isfile(fp):
                return {"status": "error", "message": f"Image file not found: {fp}"}
            img = Image.open(fp).convert("RGB")
            # Resize to consistent dimensions
            img = img.resize((width, height), Image.LANCZOS)
            pil_frames.append(img)

    if text_frames:
        for i, text in enumerate(text_frames):
            bg = _PALETTE[i % len(_PALETTE)]
            img = _render_text_frame(text, width, height, bg_color=bg)
            pil_frames.append(img)

    if not pil_frames:
        return {"status": "error", "message": "No frames to combine"}

    if not output_path:
        output_path = os.path.join(os.getcwd(), "animation.gif")

    # Ensure directory exists
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Convert frames to palette mode for smaller GIF
    gif_frames = [f.convert("P", palette=Image.ADAPTIVE, colors=256) for f in pil_frames]

    gif_frames[0].save(
        output_path,
        save_all=True,
        append_images=gif_frames[1:],
        duration=duration,
        loop=0,
        optimize=True,
    )

    file_size = os.path.getsize(output_path)

    return {
        "status": "ok",
        "output_path": output_path,
        "frame_count": len(gif_frames),
        "duration_ms": duration,
        "total_duration_ms": duration * len(gif_frames),
        "width": width,
        "height": height,
        "file_size_bytes": file_size,
    }
