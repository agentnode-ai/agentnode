"""Tests for youtube-analyzer-pack."""

import pytest
from unittest.mock import MagicMock, patch

from youtube_analyzer_pack.tool import run


# -- Input validation --

def test_missing_api_key():
    with pytest.raises(ValueError, match="api_key is required"):
        run(api_key="", operation="search")


def test_unknown_operation():
    with pytest.raises(ValueError, match="Unknown operation"):
        run(api_key="key", operation="delete_video")


def test_search_missing_query():
    with pytest.raises(ValueError, match="query is required"):
        run(api_key="key", operation="search")


def test_video_info_missing_id():
    with pytest.raises(ValueError, match="video_id is required"):
        run(api_key="key", operation="video_info")


def test_comments_missing_id():
    with pytest.raises(ValueError, match="video_id is required"):
        run(api_key="key", operation="comments")


# -- Mocked search --

@patch("youtube_analyzer_pack.tool.httpx.Client")
def test_search_success(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "items": [{
            "id": {"videoId": "v1"},
            "snippet": {
                "title": "Test Video", "description": "A test",
                "channelTitle": "TestChannel", "publishedAt": "2024-01-01",
                "thumbnails": {"high": {"url": "https://img.youtube.com/v1"}},
            },
        }],
    }
    mock_client.get.return_value = mock_resp

    result = run(api_key="key", operation="search", query="test")
    assert result["total"] == 1
    assert result["results"][0]["video_id"] == "v1"
    assert result["query"] == "test"


# -- Mocked video_info --

@patch("youtube_analyzer_pack.tool.httpx.Client")
def test_video_info_success(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "items": [{
            "snippet": {"title": "Big Video", "description": "", "channelTitle": "Ch",
                        "publishedAt": "2024-01-01", "tags": ["tech"], "categoryId": "28"},
            "statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "10"},
            "contentDetails": {"duration": "PT10M30S"},
        }],
    }
    mock_client.get.return_value = mock_resp

    result = run(api_key="key", operation="video_info", video_id="v1")
    assert result["view_count"] == 1000
    assert result["like_count"] == 50
    assert result["duration"] == "PT10M30S"


# -- video_info not found --

@patch("youtube_analyzer_pack.tool.httpx.Client")
def test_video_info_not_found(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"items": []}
    mock_client.get.return_value = mock_resp

    result = run(api_key="key", operation="video_info", video_id="missing")
    assert "error" in result
