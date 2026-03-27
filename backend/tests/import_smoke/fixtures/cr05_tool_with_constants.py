from crewai_tools import tool

VALID_LANGUAGES = {"en", "de", "fr", "es", "it", "pt", "ja", "zh"}
DEFAULT_LANG = "en"


@tool("Language Detector")
def detect_language(text: str) -> dict:
    """Detect the language of input text using simple heuristics."""
    # Very naive detection based on character ranges
    if any("\u4e00" <= c <= "\u9fff" for c in text):
        lang = "zh"
    elif any("\u3040" <= c <= "\u309f" for c in text):
        lang = "ja"
    else:
        lang = DEFAULT_LANG
    return {"language": lang, "supported": lang in VALID_LANGUAGES}
