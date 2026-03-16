"""Tests for document-redaction-pack."""


def test_run_redacts_email():
    from document_redaction_pack.tool import run

    result = run(text="Contact us at user@example.com for info.", redact_types=["email"])
    assert "redacted_text" in result
    assert "user@example.com" not in result["redacted_text"]
    assert "redactions" in result


def test_run_redacts_phone():
    from document_redaction_pack.tool import run

    result = run(text="Call (123) 456-7890 now.", redact_types=["phone"])
    assert "(123) 456-7890" not in result["redacted_text"]


def test_run_redacts_all_types():
    from document_redaction_pack.tool import run

    text = "Email: a@b.com Phone: 555-123-4567 SSN: 123-45-6789"
    result = run(text=text)
    assert "a@b.com" not in result["redacted_text"]
