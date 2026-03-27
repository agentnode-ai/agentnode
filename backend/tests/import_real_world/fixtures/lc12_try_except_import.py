from langchain.tools import tool


@tool
def read_pdf(file_path: str) -> dict:
    """Extract text from a PDF file."""
    try:
        import PyPDF2
        with open(file_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            pages = [page.extract_text() for page in reader.pages]
        return {"text": "\n".join(pages), "page_count": len(pages)}
    except ImportError:
        return {"error": "PyPDF2 not installed", "text": "", "page_count": 0}
