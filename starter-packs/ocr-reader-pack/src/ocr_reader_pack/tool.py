"""Extract text from images using Tesseract OCR or cloud OCR API."""

from __future__ import annotations

import os


def run(
    image_path: str,
    language: str = "eng",
    output_format: str = "text",
    api_key: str = "",
) -> dict:
    """Perform OCR on an image file using Tesseract or cloud API.

    Args:
        image_path: Path to the image file (PNG, JPG, TIFF, BMP, etc.).
        language: Tesseract language code (e.g. "eng", "deu", "fra").
        output_format: One of "text", "hocr", or "data" (with bounding boxes).
        api_key: If provided, use cloud OCR API instead of local Tesseract.

    Returns:
        Dictionary with text, confidence, and language.
    """
    image_path = os.path.abspath(image_path)
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    if api_key:
        return _ocr_api(image_path, language, api_key)

    return _ocr_local(image_path, language, output_format)


def _ocr_api(image_path: str, language: str, api_key: str) -> dict:
    """Perform OCR via cloud API (OCR.space)."""
    import httpx

    with open(image_path, "rb") as f:
        image_data = f.read()

    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            "https://api.ocr.space/parse/image",
            headers={"apikey": api_key},
            files={"file": (os.path.basename(image_path), image_data, "image/png")},
            data={"language": language, "isOverlayRequired": "true"},
        )
        resp.raise_for_status()
        result = resp.json()

    parsed = result.get("ParsedResults", [{}])[0]
    text = parsed.get("ParsedText", "").strip()
    overlay = parsed.get("TextOverlay", {})
    lines = overlay.get("Lines", [])

    words = []
    for line in lines:
        for word_info in line.get("Words", []):
            words.append({
                "text": word_info.get("WordText", ""),
                "x": word_info.get("Left", 0),
                "y": word_info.get("Top", 0),
                "width": word_info.get("Width", 0),
                "height": word_info.get("Height", 0),
            })

    return {
        "text": text,
        "confidence": 95.0,
        "language": language,
        "words": words,
    }


def _ocr_local(image_path: str, language: str, output_format: str) -> dict:
    """Perform OCR using local Tesseract installation."""
    import pytesseract
    from PIL import Image

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
