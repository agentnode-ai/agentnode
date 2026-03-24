"""Tests for capability gap detection (detect.py)."""
import pytest

from agentnode_sdk.detect import detect_gap


# ---------------------------------------------------------------------------
# Layer 1: ImportError → module mapping
# ---------------------------------------------------------------------------


class TestLayer1ImportError:
    def test_pdfplumber(self):
        gap = detect_gap(ImportError("No module named 'pdfplumber'"))
        assert gap is not None
        assert gap.capability == "pdf_extraction"

    def test_bs4_submodule(self):
        gap = detect_gap(ImportError("No module named 'bs4.element'"))
        assert gap is not None
        assert gap.capability == "webpage_extraction"

    def test_fitz_maps_to_pdf(self):
        gap = detect_gap(ImportError("No module named 'fitz'"))
        assert gap is not None
        assert gap.capability == "pdf_extraction"

    def test_pandas_maps_to_csv(self):
        gap = detect_gap(ImportError("No module named 'pandas'"))
        assert gap is not None
        assert gap.capability == "csv_analysis"

    def test_openpyxl_maps_to_spreadsheet(self):
        gap = detect_gap(ImportError("No module named 'openpyxl'"))
        assert gap is not None
        assert gap.capability == "spreadsheet_parsing"

    def test_selenium_maps_to_browser(self):
        gap = detect_gap(ImportError("No module named 'selenium'"))
        assert gap is not None
        assert gap.capability == "browser_navigation"

    def test_unknown_module_returns_none(self):
        gap = detect_gap(ImportError("No module named 'totally_unknown_pkg'"))
        assert gap is None


class TestLayer1Confidence:
    def test_confidence_is_high(self):
        gap = detect_gap(ImportError("No module named 'pdfplumber'"))
        assert gap.confidence == "high"

    def test_source_is_import_error(self):
        gap = detect_gap(ImportError("No module named 'pdfplumber'"))
        assert gap.source == "import_error"


# ---------------------------------------------------------------------------
# Layer 2: Error message keywords
# ---------------------------------------------------------------------------


class TestLayer2ErrorMessage:
    def test_pdfplumber_keyword(self):
        gap = detect_gap(RuntimeError("Failed: pdfplumber is not available"))
        assert gap is not None
        assert gap.capability == "pdf_extraction"

    def test_csv_parser_keyword(self):
        gap = detect_gap(RuntimeError("csv parser not found"))
        assert gap is not None
        assert gap.capability == "csv_analysis"

    def test_openpyxl_keyword(self):
        gap = detect_gap(RuntimeError("openpyxl required but missing"))
        assert gap is not None
        assert gap.capability == "spreadsheet_parsing"

    def test_chromedriver_keyword(self):
        gap = detect_gap(RuntimeError("chromedriver executable not found"))
        assert gap is not None
        assert gap.capability == "browser_navigation"

    def test_no_match_returns_none(self):
        gap = detect_gap(RuntimeError("Something completely unrelated"))
        assert gap is None


class TestLayer2NoFalsePositives:
    def test_document_your_changes(self):
        gap = detect_gap(RuntimeError("Please document your changes"))
        assert gap is None

    def test_data_processing(self):
        gap = detect_gap(RuntimeError("Error in data processing pipeline"))
        assert gap is None

    def test_invalid_pdf_password(self):
        gap = detect_gap(ValueError("Invalid PDF password"))
        assert gap is None

    def test_search_the_page(self):
        gap = detect_gap(RuntimeError("Could not search the page"))
        assert gap is None


class TestLayer2Confidence:
    def test_confidence_is_medium(self):
        gap = detect_gap(RuntimeError("pdfplumber not installed"))
        assert gap.confidence == "medium"

    def test_source_is_error_message(self):
        gap = detect_gap(RuntimeError("pdfplumber not installed"))
        assert gap.source == "error_message"


# ---------------------------------------------------------------------------
# Layer 3: Context-based
# ---------------------------------------------------------------------------


class TestLayer3Context:
    def test_pdf_extension(self):
        gap = detect_gap(RuntimeError("failed"), context={"file": "report.pdf"})
        assert gap is not None
        assert gap.capability == "pdf_extraction"

    def test_csv_extension(self):
        gap = detect_gap(RuntimeError("failed"), context={"file": "data.csv"})
        assert gap is not None
        assert gap.capability == "csv_analysis"

    def test_xlsx_extension(self):
        gap = detect_gap(RuntimeError("failed"), context={"file": "sheet.xlsx"})
        assert gap is not None
        assert gap.capability == "spreadsheet_parsing"

    def test_url_context(self):
        gap = detect_gap(
            RuntimeError("failed"), context={"url": "https://example.com"}
        )
        assert gap is not None
        assert gap.capability == "webpage_extraction"

    def test_unknown_extension_returns_none(self):
        gap = detect_gap(RuntimeError("failed"), context={"file": "data.xyz"})
        assert gap is None


class TestLayer3Confidence:
    def test_confidence_is_low(self):
        gap = detect_gap(RuntimeError("failed"), context={"file": "report.pdf"})
        assert gap.confidence == "low"

    def test_source_is_context(self):
        gap = detect_gap(RuntimeError("failed"), context={"file": "report.pdf"})
        assert gap.source == "context"


# ---------------------------------------------------------------------------
# Layer priority
# ---------------------------------------------------------------------------


class TestLayerPriority:
    def test_layer1_beats_layer2(self):
        """ImportError with known module should use L1 even if message has L2 keywords."""
        gap = detect_gap(ImportError("No module named 'pdfplumber'"))
        assert gap.confidence == "high"
        assert gap.source == "import_error"

    def test_layer2_beats_layer3(self):
        """Error message match beats context-only match."""
        gap = detect_gap(
            RuntimeError("pdfplumber not available"),
            context={"file": "data.csv"},
        )
        assert gap.confidence == "medium"
        assert gap.capability == "pdf_extraction"

    def test_layer3_fallback(self):
        """When only context matches, L3 is used."""
        gap = detect_gap(
            RuntimeError("something broke"),
            context={"file": "report.pdf"},
        )
        assert gap.confidence == "low"
        assert gap.source == "context"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_message(self):
        gap = detect_gap(RuntimeError(""))
        assert gap is None

    def test_empty_context(self):
        gap = detect_gap(RuntimeError("some error"), context={})
        assert gap is None

    def test_long_message(self):
        """Long error messages should still be scanned."""
        long_msg = "x" * 10000 + " pdfplumber " + "y" * 10000
        gap = detect_gap(RuntimeError(long_msg))
        assert gap is not None
        assert gap.capability == "pdf_extraction"
