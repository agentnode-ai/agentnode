"""Tests for icon-generator-pack."""

import os
import tempfile


def test_run_generates_icon():
    from icon_generator_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        result = run(text="AN", output_dir=tmpdir, sizes=[32, 64])
        assert "icons" in result or "paths" in result or "output_dir" in result
        # Check at least one file was created
        files = os.listdir(tmpdir)
        assert len(files) >= 1
