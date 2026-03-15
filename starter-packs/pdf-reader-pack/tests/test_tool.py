"""Tests for pdf-reader-pack."""

import os
import tempfile

import pytest


def _create_test_pdf(path: str) -> None:
    """Create a minimal PDF with reportlab or fpdf2 for testing."""
    try:
        from fpdf import FPDF
    except ImportError:
        pytest.skip("fpdf2 not installed — run: pip install fpdf2")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(200, 10, text="Hello AgentNode", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(200, 10, text="PDF extraction test", new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)


def test_run_extracts_text():
    from pdf_reader_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        _create_test_pdf(f.name)
        try:
            result = run(f.name)
            assert "text" in result
            assert "pages" in result
            assert "tables" in result
            assert len(result["pages"]) == 1
            assert "Hello AgentNode" in result["text"]
        finally:
            os.unlink(f.name)


def test_run_page_range():
    from pdf_reader_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        _create_test_pdf(f.name)
        try:
            result = run(f.name, pages="1-1")
            assert len(result["pages"]) == 1
        finally:
            os.unlink(f.name)
