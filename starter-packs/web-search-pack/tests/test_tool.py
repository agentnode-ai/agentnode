"""Tests for web-search-pack."""

import importlib
from unittest.mock import MagicMock, patch

import pytest

_has_ddgs = importlib.util.find_spec("duckduckgo_search") is not None


def test_parse_ddg_html():
    from web_search_pack.tool import _parse_ddg_html

    html = '''
    <a rel="nofollow" class="result__a" href="https://example.com">Example Title</a>
    <a class="result__snippet" href="https://example.com">This is a snippet.</a>
    <a rel="nofollow" class="result__a" href="https://other.com">Other Result</a>
    <a class="result__snippet" href="https://other.com">Another snippet here.</a>
    '''
    results = _parse_ddg_html(html, 5)
    assert len(results) == 2
    assert results[0]["title"] == "Example Title"
    assert results[0]["url"] == "https://example.com"
    assert results[0]["snippet"] == "This is a snippet."
    assert results[1]["title"] == "Other Result"


def test_parse_ddg_html_max_results():
    from web_search_pack.tool import _parse_ddg_html

    html = ''.join(
        f'<a rel="nofollow" class="result__a" href="https://r{i}.com">R{i}</a>'
        f'<a class="result__snippet" href="https://r{i}.com">S{i}</a>'
        for i in range(10)
    )
    results = _parse_ddg_html(html, 3)
    assert len(results) == 3


@pytest.mark.skipif(not _has_ddgs, reason="duckduckgo-search not installed")
@patch("duckduckgo_search.DDGS")
def test_ddgs_backend(mock_ddgs_cls):
    mock_ddgs = MagicMock()
    mock_ddgs_cls.return_value.__enter__ = MagicMock(return_value=mock_ddgs)
    mock_ddgs_cls.return_value.__exit__ = MagicMock(return_value=False)
    mock_ddgs.text.return_value = [
        {"title": "Python.org", "href": "https://python.org", "body": "Official site."},
    ]

    from web_search_pack.tool import run

    result = run("Python", max_results=2, backend="ddgs")
    assert result["results"][0]["title"] == "Python.org"


def test_run_returns_dict():
    from web_search_pack.tool import run

    mock_resp = MagicMock()
    mock_resp.text = '<a rel="nofollow" class="result__a" href="https://x.com">X</a><a class="result__snippet" href="https://x.com">Snippet</a>'
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_resp):
        result = run("test", max_results=1)
        assert "results" in result
        assert result["results"][0]["title"] == "X"
