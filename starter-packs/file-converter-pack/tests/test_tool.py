"""Tests for file-converter-pack."""

import os
import tempfile


def test_run_md_to_html():
    from file_converter_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "test.md")
        with open(src, "w", encoding="utf-8") as f:
            f.write("# Hello\n\nThis is **bold** text.")
        result = run(input_path=src, output_format="html")
        assert "output_path" in result or "content" in result or "converted" in result


def test_run_csv_to_json():
    from file_converter_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "test.csv")
        with open(src, "w", encoding="utf-8") as f:
            f.write("name,age\nAlice,30\nBob,25\n")
        result = run(input_path=src, output_format="json")
        assert "output_path" in result or "content" in result or "converted" in result
