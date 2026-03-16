"""Tests for scientific-computing-pack."""


def test_run_stats():
    from scientific_computing_pack.tool import run

    result = run(operation="stats", data=[1, 2, 3, 4, 5])
    assert result["operation"] == "stats"
    assert result["mean"] == 3.0
    assert result["count"] == 5
    assert result["min"] == 1.0
    assert result["max"] == 5.0


def test_run_linreg():
    from scientific_computing_pack.tool import run

    result = run(operation="linreg", data={"x": [1, 2, 3, 4], "y": [2, 4, 6, 8]})
    assert result["operation"] == "linreg"
    assert abs(result["slope"] - 2.0) < 0.01
    assert abs(result["intercept"]) < 0.01
    assert result["r_squared"] > 0.99
