"""
PDF reader tool using LangChain @tool decorator.
Common pattern in document-processing agent stacks.
"""

import os
import PyPDF2
from langchain.tools import tool


@tool
def read_pdf(file_path: str) -> dict:
    """
    Read a PDF file and extract text from all pages.

    Args:
        file_path: Absolute or relative path to the PDF file.

    Returns:
        dict with keys: text, page_count, file_path, error (if any)
    """
    if not os.path.exists(file_path):
        return {"error": f"File not found: {file_path}", "text": "", "page_count": 0}

    try:
        text_by_page = []
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            page_count = len(reader.pages)
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""
                text_by_page.append({"page": i + 1, "text": page_text.strip()})

        full_text = "\n\n".join(p["text"] for p in text_by_page)
        return {
            "text": full_text,
            "pages": text_by_page,
            "page_count": page_count,
            "file_path": file_path,
            "error": None,
        }
    except Exception as e:
        return {
            "error": str(e),
            "text": "",
            "page_count": 0,
            "file_path": file_path,
        }
