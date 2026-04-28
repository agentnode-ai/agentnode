"""Tests for news-aggregator-pack."""

from unittest.mock import MagicMock, patch

from news_aggregator_pack.tool import _strip_html, _matches_topic, run


# -- Pure helpers --

def test_strip_html():
    assert _strip_html("<b>Hello</b> <i>world</i>") == "Hello world"
    assert _strip_html("no tags") == "no tags"
    assert _strip_html("") == ""


def test_matches_topic_empty():
    entry = MagicMock()
    entry.title = "Anything"
    entry.summary = "Some text"
    assert _matches_topic(entry, "") is True


def test_matches_topic_found():
    entry = MagicMock()
    entry.title = "AI is changing everything"
    entry.summary = ""
    assert _matches_topic(entry, "AI") is True


def test_matches_topic_not_found():
    entry = MagicMock()
    entry.title = "Sports news"
    entry.summary = "Football match results"
    assert _matches_topic(entry, "quantum") is False


# -- Mocked run --

@patch("feedparser.parse")
@patch("httpx.Client")
def test_run_success(mock_client_cls, mock_fp_parse):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = "<rss>...</rss>"
    mock_client.get.return_value = mock_resp

    mock_entry = MagicMock()
    mock_entry.title = "Big News"
    mock_entry.link = "https://news.example.com/big"
    mock_entry.summary = "Something important happened"
    mock_entry.published = "2024-01-15T10:00:00Z"
    mock_entry.published_parsed = None

    mock_feed = MagicMock()
    mock_feed.feed.title = "Test News"
    mock_feed.entries = [mock_entry]

    mock_fp_parse.return_value = mock_feed

    result = run(feeds=["https://example.com/feed"], limit=10)
    assert result["total"] == 1
    assert result["articles"][0]["title"] == "Big News"
    assert result["articles"][0]["source"] == "Test News"


@patch("feedparser.parse")
@patch("httpx.Client")
def test_run_with_topic_filter(mock_client_cls, mock_fp_parse):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = "<rss/>"
    mock_client.get.return_value = mock_resp

    entry1 = MagicMock()
    entry1.title = "AI breakthrough"
    entry1.link = "https://example.com/ai"
    entry1.summary = "New AI model released"
    entry1.published = "2024-01-01"
    entry1.published_parsed = None

    entry2 = MagicMock()
    entry2.title = "Sports update"
    entry2.link = "https://example.com/sports"
    entry2.summary = "Game results"
    entry2.published = "2024-01-01"
    entry2.published_parsed = None

    mock_feed = MagicMock()
    mock_feed.feed.title = "Mixed News"
    mock_feed.entries = [entry1, entry2]
    mock_fp_parse.return_value = mock_feed

    result = run(feeds=["https://example.com/feed"], topic="AI", limit=10)
    assert result["total"] == 1
    assert result["articles"][0]["title"] == "AI breakthrough"
