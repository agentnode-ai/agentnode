"""PDF extraction tool wrapping pdfplumber (MIT)."""

from __future__ import annotations


def run(file_path: str, pages: str = "all") -> dict:
    """Extract text and tables from a PDF file.

    Args:
        file_path: Path to the PDF file.
        pages: Page range — "all" or "1-3" style range.

    Returns:
        dict with keys: text, pages, tables.
    """
    import pdfplumber

    result: dict = {"text": "", "pages": [], "tables": []}

    with pdfplumber.open(file_path) as pdf:
        if pages == "all":
            page_list = pdf.pages
        else:
            parts = pages.split("-")
            start = int(parts[0]) - 1
            end = int(parts[1]) if len(parts) > 1 else start + 1
            page_list = pdf.pages[start:end]

        for p in page_list:
            text = p.extract_text() or ""
            result["pages"].append(
                {"page_number": p.page_number, "content": text}
            )
            result["text"] += text + "\n"
            for t in p.extract_tables():
                result["tables"].append(
                    {"page_number": p.page_number, "data": t}
                )

    return result
