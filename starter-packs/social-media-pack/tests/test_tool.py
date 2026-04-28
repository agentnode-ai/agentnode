"""Tests for social-media-pack."""

import pytest
from unittest.mock import MagicMock, patch

from social_media_pack.tool import run


# -- Input validation --

def test_missing_token():
    with pytest.raises(ValueError, match="token is required"):
        run(platform="twitter", token="", operation="post_tweet")


def test_unsupported_platform():
    with pytest.raises(ValueError, match="Unsupported platform"):
        run(platform="tiktok", token="tok", operation="post")


def test_twitter_unknown_operation():
    with pytest.raises(ValueError, match="Unknown Twitter operation"):
        run(platform="twitter", token="tok", operation="delete_tweet")


def test_linkedin_unknown_operation():
    with pytest.raises(ValueError, match="Unknown LinkedIn operation"):
        run(platform="linkedin", token="tok", operation="delete_post")


def test_twitter_post_missing_text():
    with pytest.raises(ValueError, match="text is required"):
        run(platform="twitter", token="tok", operation="post_tweet")


def test_linkedin_share_missing_text():
    with pytest.raises(ValueError, match="text is required"):
        run(platform="linkedin", token="tok", operation="share_post")


# -- Mocked Twitter post --

@patch("social_media_pack.tool.httpx.Client")
def test_twitter_post_tweet(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": {"id": "t1", "text": "Hello world"}}
    mock_client.post.return_value = mock_resp

    result = run(platform="twitter", token="tok", operation="post_tweet", text="Hello world")
    assert result["status"] == "posted"
    assert result["platform"] == "twitter"
    assert result["tweet_id"] == "t1"


# -- Mocked Twitter get_timeline --

@patch("social_media_pack.tool.httpx.Client")
def test_twitter_get_timeline(mock_cls):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "data": [{
            "id": "t2", "text": "Tweet content", "author_id": "u1",
            "created_at": "2024-01-01T00:00:00Z",
            "public_metrics": {"like_count": 10, "retweet_count": 2, "reply_count": 1},
        }],
    }
    mock_client.get.return_value = mock_resp

    result = run(platform="twitter", token="tok", operation="get_timeline")
    assert result["total"] == 1
    assert result["tweets"][0]["likes"] == 10
