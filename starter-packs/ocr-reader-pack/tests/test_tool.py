"""Tests for ocr-reader-pack."""

import os
import tempfile
import importlib
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image

_has_pytesseract = importlib.util.find_spec("pytesseract") is not None


def _create_text_image(path: str, text: str = "Hello OCR") -> None:
    img = Image.new("RGB", (200, 50), "white")
    img.save(path)


@pytest.mark.skipif(not _has_pytesseract, reason="pytesseract not installed")
def test_text_extraction():
    from ocr_reader_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        _create_text_image(f.name)
        try:
            with patch("ocr_reader_pack.tool.pytesseract") as mock_tess:
                mock_tess.image_to_string.return_value = "Hello OCR"
                mock_tess.image_to_data.return_value = {
                    "conf": [95, 90],
                    "text": ["Hello", "OCR"],
                    "left": [10, 80],
                    "top": [10, 10],
                    "width": [60, 40],
                    "height": [20, 20],
                    "block_num": [1, 1],
                    "line_num": [1, 1],
                }
                mock_tess.Output = MagicMock()
                mock_tess.Output.DICT = "dict"

                result = run(f.name)
                assert result["text"] == "Hello OCR"
                assert result["confidence"] == 92.5
                assert result["language"] == "eng"
        finally:
            os.unlink(f.name)


@pytest.mark.skipif(not _has_pytesseract, reason="pytesseract not installed")
def test_data_output_format():
    from ocr_reader_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        _create_text_image(f.name)
        try:
            with patch("ocr_reader_pack.tool.pytesseract") as mock_tess:
                mock_tess.image_to_data.return_value = {
                    "conf": [95, 88],
                    "text": ["Test", "Word"],
                    "left": [5, 50],
                    "top": [10, 10],
                    "width": [40, 45],
                    "height": [15, 15],
                    "block_num": [1, 1],
                    "line_num": [1, 1],
                }
                mock_tess.Output = MagicMock()
                mock_tess.Output.DICT = "dict"

                result = run(f.name, output_format="data")
                assert "words" in result
                assert len(result["words"]) == 2
                assert result["words"][0]["text"] == "Test"
                assert result["words"][0]["confidence"] == 95
                assert result["text"] == "Test Word"
        finally:
            os.unlink(f.name)


@pytest.mark.skipif(not _has_pytesseract, reason="pytesseract not installed")
def test_hocr_output_format():
    from ocr_reader_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        _create_text_image(f.name)
        try:
            with patch("ocr_reader_pack.tool.pytesseract") as mock_tess:
                mock_tess.image_to_pdf_or_hocr.return_value = b"<html><body>hocr</body></html>"
                mock_tess.image_to_data.return_value = {
                    "conf": [90],
                    "text": ["Hello"],
                    "left": [10],
                    "top": [10],
                    "width": [50],
                    "height": [20],
                    "block_num": [1],
                    "line_num": [1],
                }
                mock_tess.Output = MagicMock()
                mock_tess.Output.DICT = "dict"

                result = run(f.name, output_format="hocr")
                assert "hocr" in result["text"]
                assert result["language"] == "eng"
        finally:
            os.unlink(f.name)


@pytest.mark.skipif(not _has_pytesseract, reason="pytesseract not installed")
def test_custom_language():
    from ocr_reader_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        _create_text_image(f.name)
        try:
            with patch("ocr_reader_pack.tool.pytesseract") as mock_tess:
                mock_tess.image_to_string.return_value = "Hallo Welt"
                mock_tess.image_to_data.return_value = {"conf": [92]}
                mock_tess.Output = MagicMock()
                mock_tess.Output.DICT = "dict"

                result = run(f.name, language="deu")
                assert result["language"] == "deu"
        finally:
            os.unlink(f.name)


def test_file_not_found():
    from ocr_reader_pack.tool import run

    import pytest
    with pytest.raises(FileNotFoundError):
        run("/nonexistent/image.png")
