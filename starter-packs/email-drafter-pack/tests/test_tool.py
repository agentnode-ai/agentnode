"""Tests for email-drafter-pack."""

import pytest


def test_run_professional_email():
    """Test generating a professional email."""
    from email_drafter_pack.tool import run

    result = run(
        intent="Schedule a meeting to discuss Q2 roadmap",
        tone="professional",
        recipient_name="John",
        sender_name="Alice",
    )

    assert "subject" in result
    assert "body" in result
    assert result["tone"] == "professional"
    assert "Dear John" in result["body"]
    assert "Best regards" in result["body"]
    assert "Alice" in result["body"]


def test_run_casual_email():
    """Test casual tone changes greeting and closing."""
    from email_drafter_pack.tool import run

    result = run(
        intent="Catch up over coffee this week",
        tone="casual",
        recipient_name="Bob",
    )

    assert result["tone"] == "casual"
    assert "Hey Bob" in result["body"]
    assert "Cheers" in result["body"]


def test_run_formal_email_no_recipient():
    """Test formal tone without recipient name."""
    from email_drafter_pack.tool import run

    result = run(
        intent="Request for proposal submission",
        tone="formal",
    )

    assert result["tone"] == "formal"
    assert "To Whom It May Concern" in result["body"]
    assert "Yours sincerely" in result["body"]


def test_run_urgent_email_prepends_subject():
    """Test that urgent tone prepends URGENT to subject."""
    from email_drafter_pack.tool import run

    result = run(
        intent="Server is down, need immediate fix",
        tone="urgent",
    )

    assert result["tone"] == "urgent"
    assert result["subject"].startswith("URGENT:")


def test_run_friendly_email():
    """Test friendly tone."""
    from email_drafter_pack.tool import run

    result = run(
        intent="Thanks for your help with the project",
        tone="friendly",
        recipient_name="Sarah",
    )

    assert result["tone"] == "friendly"
    assert "Hi Sarah" in result["body"]
    assert "Warm regards" in result["body"]


def test_run_explicit_subject():
    """Test that explicit Subject: line in intent is used."""
    from email_drafter_pack.tool import run

    result = run(
        intent="Subject: Project Alpha Update\nThe project is on track.",
    )

    assert result["subject"] == "Project Alpha Update"


def test_run_bullet_points_intent():
    """Test that bullet-point intent is formatted properly."""
    from email_drafter_pack.tool import run

    result = run(
        intent="- Review the contract\n- Send updated timeline\n- Confirm budget",
        tone="professional",
    )

    assert "body" in result
    # Bullet points should be converted to numbered list
    assert "1." in result["body"]
    assert "2." in result["body"]


def test_run_html_format():
    """Test HTML output format."""
    from email_drafter_pack.tool import run

    result = run(
        intent="Quick update on the project",
        format="html",
    )

    assert "<html>" in result["body"]
    assert "<br>" in result["body"]


def test_run_invalid_tone_fallback():
    """Test that invalid tone falls back to professional."""
    from email_drafter_pack.tool import run

    result = run(
        intent="Test message",
        tone="nonexistent_tone",
    )

    assert result["tone"] == "professional"


def test_run_long_subject_truncated():
    """Test that very long subjects are truncated."""
    from email_drafter_pack.tool import run

    result = run(
        intent="A" * 200,
    )

    assert len(result["subject"]) <= 83  # 77 + "..."
