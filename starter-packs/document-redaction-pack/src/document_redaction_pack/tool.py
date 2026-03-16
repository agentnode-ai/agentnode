"""Redact sensitive information from text using regex patterns."""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    ),
    "phone": re.compile(
        # Matches common formats: (123) 456-7890, 123-456-7890, +1-123-456-7890,
        # 123.456.7890, 1234567890, +1 123 456 7890
        r"(?:\+?1[\s.\-]?)?"
        r"(?:\(\d{3}\)[\s.\-]?|\d{3}[\s.\-])"
        r"\d{3}[\s.\-]?\d{4}"
        r"|\b\d{10}\b",
    ),
    "ssn": re.compile(
        # SSN: 123-45-6789 or 123 45 6789
        r"\b\d{3}[\s\-]\d{2}[\s\-]\d{4}\b",
    ),
    "credit_card": re.compile(
        # Credit card: 16 digits with optional separators (spaces or dashes)
        r"\b(?:\d{4}[\s\-]?){3}\d{4}\b",
    ),
    "ip_address": re.compile(
        # IPv4 addresses
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b",
    ),
    "date_of_birth": re.compile(
        # Common date formats that might be DOBs:
        # MM/DD/YYYY, MM-DD-YYYY, YYYY-MM-DD, DD/MM/YYYY
        # Month DD, YYYY  |  DD Month YYYY
        r"\b(?:"
        r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"
        r"|\d{4}[/\-]\d{1,2}[/\-]\d{1,2}"
        r"|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
        r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}"
        r"|\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
        r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
        r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}"
        r")\b",
        re.IGNORECASE,
    ),
}

# Placeholder labels for each type
_PLACEHOLDERS: dict[str, str] = {
    "email": "[REDACTED_EMAIL]",
    "phone": "[REDACTED_PHONE]",
    "ssn": "[REDACTED_SSN]",
    "credit_card": "[REDACTED_CC]",
    "ip_address": "[REDACTED_IP]",
    "date_of_birth": "[REDACTED_DOB]",
}

# All supported redaction types
ALL_TYPES: list[str] = list(_PATTERNS.keys())


def run(
    text: str,
    redact_types: list[str] | None = None,
) -> dict:
    """Redact sensitive information from text.

    Supported redaction types: email, phone, ssn, credit_card,
    ip_address, date_of_birth.

    Args:
        text: The input text to redact.
        redact_types: List of types to redact. If None or empty, all types
                      are redacted.

    Returns:
        Dictionary with redacted_text and a list of redaction counts.
    """
    if not text:
        return {
            "redacted_text": "",
            "redactions": [],
        }

    types_to_redact = redact_types if redact_types else ALL_TYPES

    # Validate requested types
    invalid = [t for t in types_to_redact if t not in _PATTERNS]
    if invalid:
        raise ValueError(
            f"Unknown redaction types: {invalid}. "
            f"Supported: {ALL_TYPES}"
        )

    redacted = text
    redaction_counts: list[dict[str, str | int]] = []

    for rtype in types_to_redact:
        pattern = _PATTERNS[rtype]
        placeholder = _PLACEHOLDERS[rtype]

        matches = pattern.findall(redacted)
        count = len(matches)

        if count > 0:
            redacted = pattern.sub(placeholder, redacted)
            redaction_counts.append({"type": rtype, "count": count})

    return {
        "redacted_text": redacted,
        "redactions": redaction_counts,
    }
