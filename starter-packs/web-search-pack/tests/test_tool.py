"""Tests for web-search-pack."""

import pytest


def test_run_returns_results():
    """Test that search returns structured results."""
    from web_search_pack.tool import run

    result = run("Python programming", max_results=2)
    assert "results" in result
    assert isinstance(result["results"], list)
    # DuckDuckGo may rate-limit in CI, so we just check structure
    if len(result["results"]) > 0:
        entry = result["results"][0]
        assert "title" in entry
        assert "url" in entry
        assert "snippet" in entry


def test_run_max_results_capped():
    """Test that max_results is capped at 20."""
    from web_search_pack.tool import run

    result = run("test", max_results=50)
    assert "results" in result
    assert len(result["results"]) <= 20
