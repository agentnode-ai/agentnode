"""Tests for video-generator-pack."""

import os
import tempfile


def test_hex_to_rgb():
    from video_generator_pack.tool import _hex_to_rgb

    assert _hex_to_rgb("#FF0000") == (255, 0, 0)
    assert _hex_to_rgb("#00ff00") == (0, 255, 0)
    assert _hex_to_rgb("#fff") == (255, 255, 255)


def test_gif_fallback():
    from video_generator_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "slides.gif")
        result = run(
            slides=[
                {"text": "Slide 1", "duration": 1},
                {"text": "Slide 2", "duration": 2},
            ],
            output_path=out,
            width=320,
            height=240,
        )
        assert result["status"] == "ok"
        assert result["slide_count"] == 2
        assert result["format"] == "gif"
        assert os.path.isfile(out)
        assert os.path.getsize(out) > 0


def test_single_slide():
    from video_generator_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "single.gif")
        result = run(
            slides=[{"text": "Only slide"}],
            output_path=out,
            width=200,
            height=150,
        )
        assert result["status"] == "ok"
        assert result["slide_count"] == 1


def test_custom_background():
    from video_generator_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "custom.gif")
        result = run(
            slides=[
                {"text": "Red", "background": "#FF0000"},
                {"text": "Blue", "background": "#0000FF"},
            ],
            output_path=out,
            width=200,
            height=150,
        )
        assert result["status"] == "ok"
        assert result["slide_count"] == 2


def test_no_slides_error():
    from video_generator_pack.tool import run

    result = run(slides=[])
    assert result["status"] == "error"


def test_default_duration():
    from video_generator_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "default.gif")
        result = run(
            slides=[{"text": "No duration set"}],
            output_path=out,
            width=200,
            height=150,
        )
        assert result["status"] == "ok"


def test_many_slides():
    from video_generator_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "many.gif")
        slides = [{"text": f"Slide {i}", "duration": 1} for i in range(5)]
        result = run(slides=slides, output_path=out, width=200, height=150)
        assert result["status"] == "ok"
        assert result["slide_count"] == 5
