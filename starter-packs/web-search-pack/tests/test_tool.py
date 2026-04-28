"""Tests for web-search-pack."""

from unittest.mock import MagicMock, patch


@patch("duckduckgo_search.DDGS")
def test_run_returns_results(mock_ddgs_cls):
    mock_ddgs = MagicMock()
    mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)
    mock_ddgs.text.return_value = [
        {"title": "Python.org", "href": "https://python.org", "body": "The official Python site."},
        {"title": "Learn Python", "href": "https://learn.python.org", "body": "Free tutorials."},
    ]

    from web_search_pack.tool import run

    result = run("Python programming", max_results=2)
    assert "results" in result
    assert len(result["results"]) == 2
    assert result["results"][0]["title"] == "Python.org"
    assert result["results"][0]["url"] == "https://python.org"
    assert result["results"][0]["snippet"] == "The official Python site."


@patch("duckduckgo_search.DDGS")
def test_run_max_results_capped(mock_ddgs_cls):
    mock_ddgs = MagicMock()
    mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)
    mock_ddgs.text.return_value = [{"title": f"R{i}", "href": f"https://r{i}.com", "body": "x"} for i in range(20)]

    from web_search_pack.tool import run

    result = run("test", max_results=50)
    assert len(result["results"]) <= 20


@patch("duckduckgo_search.DDGS")
def test_run_empty_results(mock_ddgs_cls):
    mock_ddgs = MagicMock()
    mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)
    mock_ddgs.text.return_value = []

    from web_search_pack.tool import run

    result = run("xyznonexistent", max_results=5)
    assert result["results"] == []
