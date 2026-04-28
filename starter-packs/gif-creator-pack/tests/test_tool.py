"""Tests for gif-creator-pack."""

import os
import tempfile


def test_hex_to_rgb():
    from gif_creator_pack.tool import _hex_to_rgb

    assert _hex_to_rgb("#FF0000") == (255, 0, 0)
    assert _hex_to_rgb("#00ff00") == (0, 255, 0)
    assert _hex_to_rgb("0000FF") == (0, 0, 255)
    assert _hex_to_rgb("#fff") == (255, 255, 255)


def test_text_frames_basic():
    from gif_creator_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "test.gif")
        result = run(text_frames=["Hello", "World"], output_path=out)

        assert result["status"] == "ok"
        assert result["frame_count"] == 2
        assert result["duration_ms"] == 500
        assert result["total_duration_ms"] == 1000
        assert result["width"] == 400
        assert result["height"] == 300
        assert os.path.isfile(out)
        assert result["file_size_bytes"] > 0


def test_custom_dimensions():
    from gif_creator_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "custom.gif")
        result = run(
            text_frames=["A", "B", "C"],
            output_path=out,
            width=200,
            height=150,
            duration=200,
        )

        assert result["status"] == "ok"
        assert result["frame_count"] == 3
        assert result["width"] == 200
        assert result["height"] == 150
        assert result["duration_ms"] == 200


def test_single_frame():
    from gif_creator_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "single.gif")
        result = run(text_frames=["Only one"], output_path=out)

        assert result["status"] == "ok"
        assert result["frame_count"] == 1


def test_no_frames_error():
    from gif_creator_pack.tool import run

    result = run()
    assert result["status"] == "error"


def test_image_frames():
    from gif_creator_pack.tool import run
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmpdir:
        paths = []
        for i, color in enumerate(["red", "blue", "green"]):
            p = os.path.join(tmpdir, f"frame_{i}.png")
            Image.new("RGB", (100, 100), color).save(p)
            paths.append(p)

        out = os.path.join(tmpdir, "from_images.gif")
        result = run(frames=paths, output_path=out, width=100, height=100)

        assert result["status"] == "ok"
        assert result["frame_count"] == 3
        assert os.path.isfile(out)


def test_missing_image_file():
    from gif_creator_pack.tool import run

    result = run(frames=["/nonexistent/path.png"])
    assert result["status"] == "error"
