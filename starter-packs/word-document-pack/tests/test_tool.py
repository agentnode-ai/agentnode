"""Tests for word-document-pack."""

import os
import tempfile


def test_run_creates_docx():
    from word_document_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "test.docx")
        result = run(
            content=[
                {"type": "heading", "text": "Test Document"},
                {"type": "paragraph", "text": "Hello World."},
            ],
            output_path=out,
            title="Test",
        )
        assert os.path.exists(out)
        assert "output_path" in result or "path" in result
