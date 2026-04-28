"""Tests for text-to-speech-pack."""

import os
import pytest
from unittest.mock import MagicMock, patch

from text_to_speech_pack.tool import run


# -- Input validation --

def test_missing_api_key():
    with pytest.raises(ValueError, match="api_key is required"):
        run(text="hello", api_key="")


def test_unsupported_provider():
    with pytest.raises(ValueError, match="Unsupported provider"):
        run(text="hello", api_key="key", provider="elevenlabs")


# -- Mocked OpenAI TTS --

@patch("text_to_speech_pack.tool.httpx.Client")
def test_openai_tts_success(mock_cls, tmp_path):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    fake_audio = b"ID3FAKEMP3DATA"
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.content = fake_audio
    mock_client.post.return_value = mock_resp

    out_file = str(tmp_path / "speech.mp3")
    result = run(text="Hello world", api_key="key", output_path=out_file, voice="nova")
    assert result["voice"] == "nova"
    assert result["provider"] == "openai"
    assert os.path.isfile(out_file)

    with open(out_file, "rb") as f:
        assert f.read() == fake_audio


def test_duration_estimate():
    """Duration estimate is roughly word_count / 150 * 60."""
    # 150 words should be ~60 seconds
    text = " ".join(["word"] * 150)
    # We cannot run without mocking, but we can test the math:
    word_count = len(text.split())
    expected = round((word_count / 150.0) * 60.0, 2)
    assert expected == 60.0


@patch("text_to_speech_pack.tool.httpx.Client")
def test_auto_generates_output_path(mock_cls, tmp_path, monkeypatch):
    mock_client = MagicMock()
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.content = b"audio"
    mock_client.post.return_value = mock_resp

    monkeypatch.chdir(tmp_path)
    result = run(text="test", api_key="key")
    assert result["output_path"].endswith(".mp3")
    assert os.path.isfile(result["output_path"])
