"""Email drafter tool that generates emails from intent using tone-aware templates."""

from __future__ import annotations

import re
import textwrap


# ---------------------------------------------------------------------------
# Tone-specific greetings, closings, and style hints
# ---------------------------------------------------------------------------

_TONE_CONFIG: dict[str, dict[str, str]] = {
    "professional": {
        "greeting": "Dear {recipient}",
        "greeting_no_name": "Hello",
        "closing": "Best regards",
        "style": "clear and concise",
    },
    "casual": {
        "greeting": "Hey {recipient}",
        "greeting_no_name": "Hey there",
        "closing": "Cheers",
        "style": "relaxed and conversational",
    },
    "formal": {
        "greeting": "Dear {recipient}",
        "greeting_no_name": "To Whom It May Concern",
        "closing": "Yours sincerely",
        "style": "respectful and formal",
    },
    "friendly": {
        "greeting": "Hi {recipient}",
        "greeting_no_name": "Hi there",
        "closing": "Warm regards",
        "style": "warm and approachable",
    },
    "urgent": {
        "greeting": "Dear {recipient}",
        "greeting_no_name": "Hello",
        "closing": "Regards",
        "style": "direct and action-oriented",
    },
}


def _extract_subject(intent: str) -> str:
    """Derive a short subject line from the intent text."""
    # If the intent already contains a line starting with "subject:" use it.
    for line in intent.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("subject:"):
            return stripped[len("subject:"):].strip()

    # Otherwise, create a subject from the first sentence / bullet.
    first_line = intent.strip().splitlines()[0].strip().lstrip("-*> ")
    # Truncate to a reasonable subject length
    if len(first_line) > 80:
        first_line = first_line[:77] + "..."
    return first_line


def _build_body_paragraphs(intent: str, tone_style: str) -> str:
    """Convert intent / bullet points into prose paragraphs."""
    lines = [l.strip() for l in intent.strip().splitlines() if l.strip()]
    # Drop any explicit "subject:" line
    lines = [l for l in lines if not l.lower().startswith("subject:")]

    if not lines:
        return ""

    # If most lines look like bullets, keep them as a list
    bullet_re = re.compile(r"^[-*>]\s+")
    bullets = [l for l in lines if bullet_re.match(l)]

    if len(bullets) > len(lines) / 2:
        # Format as numbered points
        body_parts: list[str] = []
        for idx, b in enumerate(bullets, 1):
            clean = bullet_re.sub("", b)
            body_parts.append(f"{idx}. {clean}")
        return "\n".join(body_parts)

    # Otherwise join as flowing paragraphs
    return "\n\n".join(textwrap.fill(l, width=72) for l in lines)


def run(
    intent: str,
    tone: str = "professional",
    recipient_name: str = "",
    sender_name: str = "",
    format: str = "plain",
) -> dict:
    """Generate an email from intent / bullet points.

    Args:
        intent: The purpose of the email, optionally as bullet points.
        tone: One of professional, casual, formal, friendly, urgent.
        recipient_name: Optional name of the recipient.
        sender_name: Optional name of the sender.
        format: 'plain' for plain-text, 'html' for basic HTML wrapping.

    Returns:
        dict with keys: subject, body, tone.
    """
    tone = tone.lower().strip()
    if tone not in _TONE_CONFIG:
        tone = "professional"

    cfg = _TONE_CONFIG[tone]

    # Greeting
    if recipient_name.strip():
        greeting = cfg["greeting"].format(recipient=recipient_name.strip())
    else:
        greeting = cfg["greeting_no_name"]

    # Subject
    subject = _extract_subject(intent)
    if tone == "urgent" and not subject.upper().startswith("URGENT"):
        subject = f"URGENT: {subject}"

    # Body paragraphs
    body_content = _build_body_paragraphs(intent, cfg["style"])

    # Closing
    closing = cfg["closing"]
    if sender_name.strip():
        sign_off = f"{closing},\n{sender_name.strip()}"
    else:
        sign_off = f"{closing}"

    # Assemble
    body = f"{greeting},\n\n{body_content}\n\n{sign_off}"

    if format.lower() == "html":
        body_html = body.replace("\n", "<br>\n")
        body = f"<html><body>\n{body_html}\n</body></html>"

    return {
        "subject": subject,
        "body": body,
        "tone": tone,
    }
