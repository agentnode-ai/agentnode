"""Tests for data-visualizer-pack."""

import os
import tempfile


def test_bar_chart():
    from data_visualizer_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "bar.png")
        result = run(
            data={"x": ["A", "B", "C"], "y": [10, 20, 30]},
            chart_type="bar",
            output_path=out,
        )
        assert "chart_path" in result
        assert result["chart_type"] == "bar"
        assert os.path.isfile(out)
        assert os.path.getsize(out) > 0


def test_line_chart():
    from data_visualizer_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "line.png")
        result = run(
            data={"x": [1, 2, 3, 4], "y": [10, 20, 15, 25]},
            chart_type="line",
            output_path=out,
        )
        assert "chart_path" in result
        assert result["chart_type"] == "line"
        assert os.path.isfile(out)


def test_scatter_chart():
    from data_visualizer_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "scatter.png")
        result = run(
            data={"x": [1, 2, 3], "y": [4, 5, 6]},
            chart_type="scatter",
            output_path=out,
        )
        assert "chart_path" in result
        assert result["chart_type"] == "scatter"


def test_pie_chart():
    from data_visualizer_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "pie.png")
        result = run(
            data={"labels": ["A", "B", "C"], "values": [30, 50, 20]},
            chart_type="pie",
            output_path=out,
        )
        assert "chart_path" in result
        assert result["chart_type"] == "pie"


def test_histogram():
    from data_visualizer_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "hist.png")
        result = run(
            data={"values": [1, 2, 2, 3, 3, 3, 4, 4, 5]},
            chart_type="histogram",
            output_path=out,
        )
        assert "chart_path" in result
        assert result["chart_type"] == "histogram"


def test_multi_series():
    from data_visualizer_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "multi.png")
        result = run(
            data={"x": [1, 2, 3], "y1": [10, 20, 30], "y2": [5, 15, 25]},
            chart_type="line",
            output_path=out,
        )
        assert "chart_path" in result


def test_dataframe_style():
    from data_visualizer_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "df.png")
        result = run(
            data={"month": [1, 2, 3], "sales": [100, 150, 200]},
            chart_type="bar",
            output_path=out,
        )
        assert "chart_path" in result


def test_custom_title():
    from data_visualizer_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "titled.png")
        result = run(
            data={"x": ["A"], "y": [1]},
            chart_type="bar",
            title="My Chart",
            output_path=out,
        )
        assert result["title"] == "My Chart"


def test_invalid_chart_type():
    from data_visualizer_pack.tool import run

    result = run(data={"x": [1], "y": [1]}, chart_type="radar")
    assert "error" in result


def test_pie_missing_data():
    from data_visualizer_pack.tool import run

    result = run(data={}, chart_type="pie")
    assert "error" in result


def test_histogram_missing_values():
    from data_visualizer_pack.tool import run

    result = run(data={}, chart_type="histogram")
    assert "error" in result


def test_auto_output_path():
    from data_visualizer_pack.tool import run

    result = run(data={"x": [1, 2], "y": [3, 4]}, chart_type="bar")
    assert "chart_path" in result
    path = result["chart_path"]
    assert os.path.isfile(path)
    os.unlink(path)
