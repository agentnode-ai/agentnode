"""Tests for powerpoint-generator-pack."""

import os
import tempfile


def test_run_creates_pptx():
    from powerpoint_generator_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "test.pptx")
        result = run(
            slides=[
                {"title": "Slide 1", "content": "Hello World"},
                {"title": "Slide 2", "content": "Goodbye"},
            ],
            output_path=out,
            title="Test Presentation",
        )
        assert os.path.exists(out)
