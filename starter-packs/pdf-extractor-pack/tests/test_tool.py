"""Tests for pdf-extractor-pack."""

import os
import tempfile

import pytest


def _create_test_pdf(path: str, text: str = "Hello AgentNode PDF test") -> None:
    try:
        from fpdf import FPDF
    except ImportError:
        pytest.skip("fpdf2 not installed")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(200, 10, text=text, new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)


def test_parse_page_range():
    from pdf_extractor_pack.tool import _parse_page_range

    assert _parse_page_range("all", 5) == [0, 1, 2, 3, 4]
    assert _parse_page_range("1-3", 5) == [0, 1, 2]
    assert _parse_page_range("2", 5) == [1]
    assert _parse_page_range("1,3,5", 5) == [0, 2, 4]
    assert _parse_page_range("1-2,4", 5) == [0, 1, 3]
    assert _parse_page_range("10", 3) == []


def test_extract_text():
    from pdf_extractor_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        _create_test_pdf(f.name)
        try:
            result = run(f.name)
            assert "Hello AgentNode" in result["text"]
            assert result["page_count"] == 1
            assert isinstance(result["tables"], list)
            assert isinstance(result["metadata"], dict)
        finally:
            os.unlink(f.name)


def test_extract_specific_pages():
    from pdf_extractor_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        _create_test_pdf(f.name)
        try:
            result = run(f.name, pages="1")
            assert "Hello AgentNode" in result["text"]
        finally:
            os.unlink(f.name)


def test_extract_tables_flag():
    from pdf_extractor_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        _create_test_pdf(f.name)
        try:
            result = run(f.name, extract_tables=False)
            assert result["tables"] == []
        finally:
            os.unlink(f.name)


def test_file_not_found():
    from pdf_extractor_pack.tool import run

    with pytest.raises(FileNotFoundError):
        run("/nonexistent/file.pdf")


def test_images_default_off():
    from pdf_extractor_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        _create_test_pdf(f.name)
        try:
            result = run(f.name, extract_images=False)
            assert result["images"] == []
        finally:
            os.unlink(f.name)
