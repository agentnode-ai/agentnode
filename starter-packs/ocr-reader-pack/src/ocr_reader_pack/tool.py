"""Extract text from images using Tesseract OCR."""

from __future__ import annotations

import os

import pytesseract
from PIL import Image


def run(
    image_path: str,
    language: str = "eng",
    output_format: str = "text",
) -> dict:
    """Perform OCR on an image file using Tesseract.

    Args:
        image_path: Path to the image file (PNG, JPG, TIFF, BMP, etc.).
        language: Tesseract language code (e.g. "eng", "deu", "fra").
        output_format: One of "text", "hocr", or "data" (with bounding boxes).

    Returns:
        Dictionary with text, confidence, and language.
    """
    image_path = os.path.abspath(image_path)
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    img = Image.open(image_path)

    output_format = output_format.strip().lower()

    if output_format == "hocr":
        # Return hOCR XML output
        hocr_output = pytesseract.image_to_pdf_or_hocr(
            img, lang=language, extension="hocr"
        )
        text = hocr_output.decode("utf-8") if isinstance(hocr_output, bytes) else hocr_output

        # Get confidence from data output
        data = pytesseract.image_to_data(img, lang=language, output_type=pytesseract.Output.DICT)
        confidences = [
            int(c) for c in data.get("conf", []) if str(c).lstrip("-").isdigit() and int(c) >= 0
        ]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "text": text,
            "confidence": round(avg_confidence, 2),
            "language": language,
        }

    if output_format == "data":
        # Return structured data with bounding boxes
        data = pytesseract.image_to_data(img, lang=language, output_type=pytesseract.Output.DICT)

        words: list[dict] = []
        confidences: list[int] = []

        n = len(data.get("text", []))
        for i in range(n):
            word = data["text"][i].strip()
            conf = data["conf"][i]
            if not word:
                continue
            conf_int = int(conf) if str(conf).lstrip("-").isdigit() else 0
            if conf_int >= 0:
                confidences.append(conf_int)

            words.append({
                "text": word,
                "confidence": conf_int,
                "x": data["left"][i],
                "y": data["top"][i],
                "width": data["width"][i],
                "height": data["height"][i],
                "block": data["block_num"][i],
                "line": data["line_num"][i],
            })

        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        # Build full text from words
        full_text = " ".join(w["text"] for w in words)

        return {
            "text": full_text,
            "confidence": round(avg_confidence, 2),
            "language": language,
            "words": words,
        }

    # Default: plain text
    text = pytesseract.image_to_string(img, lang=language).strip()

    # Get confidence
    data = pytesseract.image_to_data(img, lang=language, output_type=pytesseract.Output.DICT)
    confidences = [
        int(c) for c in data.get("conf", []) if str(c).lstrip("-").isdigit() and int(c) >= 0
    ]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    return {
        "text": text,
        "confidence": round(avg_confidence, 2),
        "language": language,
    }
