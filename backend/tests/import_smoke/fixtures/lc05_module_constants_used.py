from langchain.tools import tool

MAX_LENGTH = 1000
SUPPORTED_FORMATS = ["txt", "md", "csv"]
DEFAULT_ENCODING = "utf-8"


@tool
def validate_input(text: str, format: str = "txt") -> dict:
    """Validate input text against supported formats and length limits."""
    errors = []
    if len(text) > MAX_LENGTH:
        errors.append(f"Text exceeds maximum length of {MAX_LENGTH}")
    if format not in SUPPORTED_FORMATS:
        errors.append(f"Format '{format}' not in {SUPPORTED_FORMATS}")
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "encoding": DEFAULT_ENCODING,
    }
