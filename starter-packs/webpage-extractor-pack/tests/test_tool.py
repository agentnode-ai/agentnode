"""Tests for webpage-extractor-pack."""

from unittest.mock import patch, MagicMock


@patch("trafilatura.extract")
@patch("trafilatura.fetch_url")
def test_run_returns_structure(mock_fetch, mock_extract):
    mock_fetch.return_value = "<html><head><title>Example</title></head><body><p>Hello world</p></body></html>"
    mock_extract.side_effect = [
        "Hello world",
        '{"title": "Example Domain"}',
    ]

    from webpage_extractor_pack.tool import run

    result = run("https://example.com")
    assert result["url"] == "https://example.com"
    assert result["title"] == "Example Domain"
    assert len(result["text"]) > 0


@patch("trafilatura.fetch_url")
def test_run_bad_url(mock_fetch):
    mock_fetch.return_value = None

    from webpage_extractor_pack.tool import run

    result = run("https://this-url-does-not-exist-12345.invalid")
    assert "error" in result
    assert result["text"] == ""


@patch("trafilatura.extract")
@patch("trafilatura.fetch_url")
def test_run_with_links(mock_fetch, mock_extract):
    mock_fetch.return_value = "<html><body><p>Text with <a href='http://x.com'>link</a></p></body></html>"
    mock_extract.side_effect = ["Text with [link](http://x.com)", '{"title": "Test"}']

    from webpage_extractor_pack.tool import run

    result = run("https://example.com", include_links=True)
    assert "link" in result["text"]
