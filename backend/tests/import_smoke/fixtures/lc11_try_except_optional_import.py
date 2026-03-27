from langchain.tools import tool

try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False


@tool
def extract_pdf_text(file_path: str) -> dict:
    """Extract text from a PDF file."""
    if not HAS_PYPDF2:
        return {"error": "PyPDF2 is not installed", "text": ""}
    with open(file_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        pages = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
    return {"text": "\n".join(pages), "page_count": len(pages)}
