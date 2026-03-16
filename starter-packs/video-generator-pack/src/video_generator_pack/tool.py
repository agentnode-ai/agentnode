"""Video/slideshow generator using Pillow (and optionally ffmpeg)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

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


def _render_slide(
    text: str,
    width: int,
    height: int,
    bg_color: str = "#000000",
    text_color: str = "#FFFFFF",
) -> Image.Image:
    """Render a single slide as a PIL Image."""
    bg_rgb = _hex_to_rgb(bg_color)
    txt_rgb = _hex_to_rgb(text_color)
    img = Image.new("RGB", (width, height), bg_rgb)
    draw = ImageDraw.Draw(img)

    # Choose font size relative to image dimensions
    font_size = max(20, min(width, height) // 15)
    font = _get_font(font_size)

    # Wrap text to fit the image width (roughly 80% of width)
    max_chars = max(10, int(width * 0.8 / (font_size * 0.6)))
    wrapped = textwrap.fill(text, width=max_chars)
    lines = wrapped.split("\n")

    # Calculate total text height
    line_height = font_size + 8
    total_text_height = line_height * len(lines)

    # Centre vertically
    y = max(0, (height - total_text_height) // 2)

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_width = bbox[2] - bbox[0]
        x = max(0, (width - text_width) // 2)
        draw.text((x, y), line, fill=txt_rgb, font=font)
        y += line_height

    return img


def _has_ffmpeg() -> bool:
    """Check whether ffmpeg is available on PATH."""
    return shutil.which("ffmpeg") is not None


def run(
    slides: list[dict],
    output_path: str = "",
    fps: int = 1,
    width: int = 1920,
    height: int = 1080,
) -> dict:
    """Generate a slideshow video from text slides.

    Parameters
    ----------
    slides : list[dict]
        Each dict: ``{"text": str, "duration": int, "background": str}``.
        ``duration`` is in seconds. ``background`` is a hex colour (default ``"#000000"``).
    output_path : str
        Destination file path. Auto-generated if empty.
    fps : int
        Frames per second (default 1). Higher = smoother transitions when
        combined with ffmpeg.
    width, height : int
        Slide dimensions in pixels.

    Returns
    -------
    dict with ``output_path``, ``slide_count``, and ``format``.
    """
    if not slides:
        return {"status": "error", "message": "At least one slide is required"}

    use_ffmpeg = _has_ffmpeg()

    # Determine output format
    if not output_path:
        ext = ".mp4" if use_ffmpeg else ".gif"
        output_path = os.path.join(os.getcwd(), f"slideshow{ext}")

    # Render frames
    rendered_frames: list[tuple[Image.Image, int]] = []
    for i, slide in enumerate(slides):
        text = slide.get("text", f"Slide {i + 1}")
        duration = max(1, int(slide.get("duration", 3)))
        bg = slide.get("background", "#000000")
        text_color = slide.get("text_color", "#FFFFFF")
        img = _render_slide(text, width, height, bg_color=bg, text_color=text_color)
        rendered_frames.append((img, duration))

    if use_ffmpeg and output_path.lower().endswith((".mp4", ".avi", ".mkv", ".mov")):
        # Save individual frames to a temp directory and use ffmpeg
        out_format = os.path.splitext(output_path)[1].lstrip(".").lower()
        with tempfile.TemporaryDirectory() as tmpdir:
            frame_index = 0
            for img, duration in rendered_frames:
                num_frames = max(1, duration * fps)
                for _ in range(num_frames):
                    frame_path = os.path.join(tmpdir, f"frame_{frame_index:06d}.png")
                    img.save(frame_path, "PNG")
                    frame_index += 1

            # Build ffmpeg command
            cmd = [
                "ffmpeg", "-y",
                "-framerate", str(fps),
                "-i", os.path.join(tmpdir, "frame_%06d.png"),
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", "fast",
                output_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                return {
                    "status": "error",
                    "message": f"ffmpeg failed: {result.stderr[:500]}",
                }
        return {
            "status": "ok",
            "output_path": output_path,
            "slide_count": len(slides),
            "format": out_format,
            "total_frames": frame_index,
        }
    else:
        # Fall back to animated GIF
        if not output_path.lower().endswith(".gif"):
            output_path = os.path.splitext(output_path)[0] + ".gif"

        gif_frames: list[Image.Image] = []
        durations: list[int] = []
        for img, duration in rendered_frames:
            gif_frames.append(img)
            durations.append(duration * 1000)  # PIL expects milliseconds

        gif_frames[0].save(
            output_path,
            save_all=True,
            append_images=gif_frames[1:],
            duration=durations,
            loop=0,
        )
        return {
            "status": "ok",
            "output_path": output_path,
            "slide_count": len(slides),
            "format": "gif",
        }
