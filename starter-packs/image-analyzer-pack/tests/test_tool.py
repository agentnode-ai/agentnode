"""Tests for image-analyzer-pack."""

import os
import tempfile

from PIL import Image


def _create_test_image(path: str, width: int = 100, height: int = 80, color: str = "red") -> None:
    img = Image.new("RGB", (width, height), color)
    img.save(path)


def test_info_operation():
    from image_analyzer_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        _create_test_image(f.name, 200, 150)
        try:
            result = run(f.name, operations=["info"])
            assert result["status"] == "ok"
            info = result["info"]
            assert info["width"] == 200
            assert info["height"] == 150
            assert info["mode"] == "RGB"
            assert info["megapixels"] == 0.03
            assert info["file_size_bytes"] > 0
        finally:
            os.unlink(f.name)


def test_colors_operation():
    from image_analyzer_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        _create_test_image(f.name, color="blue")
        try:
            result = run(f.name, operations=["colors"])
            assert result["status"] == "ok"
            colors = result["colors"]
            assert isinstance(colors, list)
            assert len(colors) > 0
            assert "hex" in colors[0]
            assert "rgb" in colors[0]
            assert "percentage" in colors[0]
        finally:
            os.unlink(f.name)


def test_histogram_operation():
    from image_analyzer_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        _create_test_image(f.name, color="green")
        try:
            result = run(f.name, operations=["histogram"])
            assert result["status"] == "ok"
            hist = result["histogram"]
            for channel in ("red", "green", "blue"):
                assert channel in hist
                assert "mean" in hist[channel]
                assert "std" in hist[channel]
                assert "min" in hist[channel]
                assert "max" in hist[channel]
                assert "median" in hist[channel]
        finally:
            os.unlink(f.name)


def test_exif_operation():
    from image_analyzer_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        _create_test_image(f.name)
        try:
            result = run(f.name, operations=["exif"])
            assert result["status"] == "ok"
            assert "exif" in result
            assert isinstance(result["exif"], dict)
        finally:
            os.unlink(f.name)


def test_default_operations():
    from image_analyzer_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        _create_test_image(f.name)
        try:
            result = run(f.name)
            assert result["status"] == "ok"
            assert "info" in result
            assert "colors" in result
            assert "histogram" in result
        finally:
            os.unlink(f.name)


def test_invalid_operation():
    from image_analyzer_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        _create_test_image(f.name)
        try:
            result = run(f.name, operations=["invalid_op"])
            assert result["status"] == "error"
        finally:
            os.unlink(f.name)


def test_missing_file():
    from image_analyzer_pack.tool import run

    result = run("/nonexistent/image.png")
    assert result["status"] == "error"


def test_no_path():
    from image_analyzer_pack.tool import run

    result = run("")
    assert result["status"] == "error"


def test_solid_color_histogram():
    from image_analyzer_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        Image.new("RGB", (50, 50), (255, 0, 0)).save(f.name)
        try:
            result = run(f.name, operations=["histogram"])
            hist = result["histogram"]
            assert hist["red"]["mean"] == 255.0
            assert hist["red"]["std"] == 0.0
            assert hist["green"]["mean"] == 0.0
            assert hist["blue"]["mean"] == 0.0
        finally:
            os.unlink(f.name)
