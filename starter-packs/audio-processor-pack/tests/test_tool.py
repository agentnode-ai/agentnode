"""Tests for audio-processor-pack."""

import os
import struct
import tempfile
import wave


def _create_wav(path: str, duration_ms: int = 500, sample_rate: int = 44100) -> None:
    n_frames = int(sample_rate * duration_ms / 1000)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(n_frames):
            value = int(16000 * (i % 100) / 100)
            wf.writeframes(struct.pack("<h", value))


def test_info_operation():
    from audio_processor_pack.tool import run

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        _create_wav(f.name, duration_ms=1000)
        try:
            result = run(operation="info", file_path=f.name)
            assert result["status"] == "ok"
            info = result["info"]
            assert info["channels"] == 1
            assert info["sample_rate"] == 44100
            assert info["duration_ms"] > 0
            assert info["file_size_bytes"] > 0
        finally:
            os.unlink(f.name)


def test_trim_operation():
    from audio_processor_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "source.wav")
        _create_wav(src, duration_ms=2000)
        out = os.path.join(tmpdir, "trimmed.wav")

        result = run(operation="trim", file_path=src, output_path=out, start_ms=0, end_ms=1000)
        assert result["status"] == "ok"
        assert result["trimmed_duration_ms"] <= 1050
        assert os.path.isfile(out)


def test_volume_operation():
    from audio_processor_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "source.wav")
        _create_wav(src, duration_ms=500)
        out = os.path.join(tmpdir, "louder.wav")

        result = run(operation="volume", file_path=src, output_path=out, change_db=6.0)
        assert result["status"] == "ok"
        assert result["change_db"] == 6.0
        assert os.path.isfile(out)


def test_merge_operation():
    from audio_processor_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        f1 = os.path.join(tmpdir, "a.wav")
        f2 = os.path.join(tmpdir, "b.wav")
        _create_wav(f1, duration_ms=300)
        _create_wav(f2, duration_ms=400)
        out = os.path.join(tmpdir, "merged.wav")

        result = run(operation="merge", file_path=f1, output_path=out, file_paths=[f2])
        assert result["status"] == "ok"
        assert result["file_count"] == 2
        assert result["total_duration_ms"] >= 650
        assert os.path.isfile(out)


def test_merge_needs_two_files():
    from audio_processor_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        f1 = os.path.join(tmpdir, "only.wav")
        _create_wav(f1, duration_ms=300)

        result = run(operation="merge", file_path=f1)
        assert result["status"] == "error"


def test_unknown_operation():
    from audio_processor_pack.tool import run

    with tempfile.TemporaryDirectory() as tmpdir:
        f1 = os.path.join(tmpdir, "test.wav")
        _create_wav(f1)

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
