"""Tests for speech-to-text-pack."""

import pytest
from unittest.mock import MagicMock, patch

from speech_to_text_pack.tool import run


# -- Input validation --

def test_file_not_found():
    with pytest.raises(FileNotFoundError, match="Audio file not found"):
        run(audio_path="/nonexistent/audio.mp3")


# -- Mocked local Whisper --

@patch("speech_to_text_pack.tool.whisper")
@patch("os.path.isfile", return_value=True)
def test_local_transcribe(mock_isfile, mock_whisper):
    mock_model = MagicMock()
    mock_whisper.load_model.return_value = mock_model
    mock_model.transcribe.return_value = {
        "text": "Hello world, this is a test.",
        "language": "en",
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.5, "text": " Hello world,"},
            {"id": 1, "start": 2.5, "end": 5.0, "text": " this is a test."},
        ],
    }

    result = run(audio_path="/tmp/test.wav", model="base")
    assert result["text"] == "Hello world, this is a test."
    assert result["language"] == "en"
    assert result["duration"] == 5.0
    assert len(result["segments"]) == 2
    mock_whisper.load_model.assert_called_once_with("base")


@patch("speech_to_text_pack.tool.whisper")
@patch("os.path.isfile", return_value=True)
def test_local_transcribe_with_language(mock_isfile, mock_whisper):
    mock_model = MagicMock()
    mock_whisper.load_model.return_value = mock_model
    mock_model.transcribe.return_value = {
        "text": "Bonjour le monde.",
        "language": "fr",
        "segments": [],
    }

    result = run(audio_path="/tmp/test.wav", model="small", language="fr")
    assert result["language"] == "fr"
    mock_model.transcribe.assert_called_once_with("/tmp/test.wav", language="fr")


# -- Mocked API Whisper --

@patch("speech_to_text_pack.tool.httpx.Client")
@patch("builtins.open", create=True)
@patch("os.path.isfile", return_value=True)
@patch("os.path.basename", return_value="test.mp3")
def test_api_transcribe(mock_basename, mock_isfile, mock_open, mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "text": "API transcribed text",
        "language": "en",
        "duration": 10.0,
        "segments": [{"id": 0, "start": 0.0, "end": 10.0, "text": "API transcribed text"}],
    }
    mock_client.post.return_value = mock_resp

    mock_file = MagicMock()
    mock_open.return_value.__enter__ = MagicMock(return_value=mock_file)
    mock_open.return_value.__exit__ = MagicMock(return_value=False)

    result = run(audio_path="/tmp/test.mp3", api_key="sk-key")
    assert result["text"] == "API transcribed text"
    assert result["duration"] == 10.0
