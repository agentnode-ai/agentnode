"""Text translation tool using deep-translator's GoogleTranslator."""

from __future__ import annotations


def run(
    text: str,
    target_language: str = "en",
    source_language: str = "auto",
) -> dict:
    """Translate text between languages using Google Translate.

    Args:
        text: The text to translate.
        target_language: Target language code (e.g. 'en', 'fr', 'de', 'es', 'ja').
        source_language: Source language code or 'auto' for auto-detection.

    Returns:
        dict with keys: translated_text, source_language, target_language.
    """
    from deep_translator import GoogleTranslator

    if not text.strip():
        return {
            "translated_text": "",
            "source_language": source_language,
            "target_language": target_language,
        }

    translator = GoogleTranslator(source=source_language, target=target_language)
    translated = translator.translate(text)

    return {
        "translated_text": translated,
        "source_language": source_language,
        "target_language": target_language,
    }
