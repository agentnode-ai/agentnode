"""Tests for audio-processor-pack."""

import os
import tempfile
from unittest.mock import MagicMock, patch


def _mock_audio(duration_ms=1000, channels=1, frame_rate=44100):
    audio = MagicMock()
    audio.__len__ = MagicMock(return_value=duration_ms)
    audio.channels = channels
    audio.frame_rate = frame_rate
    audio.sample_width = 2
    audio.frame_count.return_value = frame_rate * duration_ms // 1000
    audio.dBFS = -20.0
    audio.max_dBFS = -3.0
    audio.__getitem__ = MagicMock(return_value=audio)
    audio.__add__ = MagicMock(return_value=audio)
    audio.__iadd__ = MagicMock(return_value=audio)
    audio.export = MagicMock()
    return audio


@patch("audio_processor_pack.tool._load_audio")
@patch("audio_processor_pack.tool.mediainfo", return_value={})
def test_info_operation(mock_mediainfo, mock_load):
    from audio_processor_pack.tool import run

    mock_load.return_value = _mock_audio(duration_ms=1000)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"\x00" * 100)
        f.flush()
        try:
            result = run(operation="info", file_path=f.name)
            assert result["status"] == "ok"
            info = result["info"]
            assert info["channels"] == 1
            assert info["sample_rate"] == 44100
            assert info["duration_ms"] == 1000
        finally:
            os.unlink(f.name)


@patch("audio_processor_pack.tool._load_audio")
def test_trim_operation(mock_load):
    from audio_processor_pack.tool import run

    mock_audio = _mock_audio(duration_ms=2000)
    trimmed = _mock_audio(duration_ms=1000)
    mock_audio.__getitem__.return_value = trimmed
    mock_load.return_value = mock_audio

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "source.wav")
        with open(src, "wb") as f:
            f.write(b"\x00" * 100)
        out = os.path.join(tmpdir, "trimmed.wav")

        result = run(operation="trim", file_path=src, output_path=out, start_ms=0, end_ms=1000)
        assert result["status"] == "ok"
        assert result["trimmed_duration_ms"] == 1000
        trimmed.export.assert_called_once()


@patch("audio_processor_pack.tool._load_audio")
def test_volume_operation(mock_load):
    from audio_processor_pack.tool import run

    mock_audio = _mock_audio(duration_ms=500)
    louder = _mock_audio(duration_ms=500)
    mock_audio.__add__.return_value = louder
    mock_load.return_value = mock_audio

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "source.wav")
        with open(src, "wb") as f:
            f.write(b"\x00" * 100)
        out = os.path.join(tmpdir, "louder.wav")

        result = run(operation="volume", file_path=src, output_path=out, change_db=6.0)
        assert result["status"] == "ok"
        assert result["change_db"] == 6.0
        louder.export.assert_called_once()


@patch("audio_processor_pack.tool._load_audio")
def test_merge_operation(mock_load):
    from audio_processor_pack.tool import run

    audio1 = _mock_audio(duration_ms=300)
    audio2 = _mock_audio(duration_ms=400)
    combined = _mock_audio(duration_ms=700)
    audio1.__iadd__.return_value = combined
    combined.__iadd__ = MagicMock(return_value=combined)
    audio1.__add__ = MagicMock(return_value=combined)

    mock_load.side_effect = [audio1, audio2]

    with tempfile.TemporaryDirectory() as tmpdir:
        f1 = os.path.join(tmpdir, "a.wav")
        f2 = os.path.join(tmpdir, "b.wav")
        for p in (f1, f2):
            with open(p, "wb") as f:
                f.write(b"\x00" * 100)
        out = os.path.join(tmpdir, "merged.wav")

        result = run(operation="merge", file_path=f1, output_path=out, file_paths=[f2])
        assert result["status"] == "ok"
        assert result["file_count"] == 2


def test_merge_needs_two_files():
    from audio_processor_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        f1 = os.path.join(tmpdir, "only.wav")
        with open(f1, "wb") as f:
            f.write(b"\x00" * 100)

        result = run(operation="merge", file_path=f1)
        assert result["status"] == "error"


def test_unknown_operation():
    from audio_processor_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        f1 = os.path.join(tmpdir, "test.wav")
        with open(f1, "wb") as f:
            f.write(b"\x00" * 100)

        result = run(operation="reverse", file_path=f1)
        assert result["status"] == "error"


def test_missing_file():
    from audio_processor_pack.tool import run

    result = run(operation="info", file_path="/nonexistent/audio.wav")
    assert result["status"] == "error"


def test_no_file_path():
    from audio_processor_pack.tool import run

    result = run(operation="info", file_path="")
    assert result["status"] == "error"
