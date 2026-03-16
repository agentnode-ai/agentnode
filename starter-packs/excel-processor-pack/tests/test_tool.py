"""Tests for excel-processor-pack."""

import os
import tempfile


def test_run_create_and_read():
    from excel_processor_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "test.xlsx")
        create_result = run(
            operation="create",
            data=[["Name", "Age"], ["Alice", 30], ["Bob", 25]],
            output_path=out,
        )
        assert os.path.exists(out)

        read_result = run(operation="read", file_path=out)
        assert "data" in read_result or "rows" in read_result


def test_run_create_default():
    from excel_processor_pack.tool import run

    result = run(operation="create", data=[["A", "B"], [1, 2]])
    assert "output_path" in result or "path" in result
