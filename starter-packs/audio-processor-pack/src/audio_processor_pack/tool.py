"""Audio processing tool using pydub."""

from __future__ import annotations

import os

from pydub import AudioSegment
from pydub.utils import mediainfo


def _load_audio(file_path: str) -> AudioSegment:
    """Load an audio file, auto-detecting format from extension."""
    ext = os.path.splitext(file_path)[1].lstrip(".").lower()
    format_map = {
        "mp3": "mp3",
        "wav": "wav",
        "ogg": "ogg",
        "flac": "flac",
        "m4a": "m4a",
        "aac": "aac",
        "wma": "wma",
        "webm": "webm",
    }
    fmt = format_map.get(ext, ext)
    return AudioSegment.from_file(file_path, format=fmt)


def _auto_output(file_path: str, output_path: str, suffix: str = "_out", fmt: str | None = None) -> str:
    """Generate an output path if none provided."""
    if output_path:
        return output_path
    base, ext = os.path.splitext(file_path)
    if fmt:
        ext = f".{fmt}"
    return f"{base}{suffix}{ext}"


def _info(file_path: str, output_path: str = "", **kwargs) -> dict:
    """Get audio file metadata."""
    audio = _load_audio(file_path)
    info = {
        "duration_ms": len(audio),
        "duration_seconds": round(len(audio) / 1000.0, 2),
        "channels": audio.channels,
        "sample_rate": audio.frame_rate,
        "sample_width": audio.sample_width,
        "frame_count": audio.frame_count(),
        "dbfs": round(audio.dBFS, 2) if audio.dBFS != float("-inf") else None,
        "max_dbfs": round(audio.max_dBFS, 2),
        "file_size_bytes": os.path.getsize(file_path),
    }
    # Try to get additional info via mediainfo
    try:
        mi = mediainfo(file_path)
        info["codec"] = mi.get("codec_name") or mi.get("codec_long_name")
        info["bit_rate"] = mi.get("bit_rate")
        info["format_name"] = mi.get("format_name")
    except Exception:
        pass
    return {"status": "ok", "file_path": file_path, "info": info}


def _trim(file_path: str, output_path: str, **kwargs) -> dict:
    """Trim audio to a specific range."""
    start_ms = int(kwargs.get("start_ms", 0))
    end_ms = kwargs.get("end_ms")
    audio = _load_audio(file_path)
    if end_ms is not None:
        end_ms = int(end_ms)
        trimmed = audio[start_ms:end_ms]
    else:
        trimmed = audio[start_ms:]
    out = _auto_output(file_path, output_path, suffix="_trimmed")
    fmt = os.path.splitext(out)[1].lstrip(".").lower() or "mp3"
    trimmed.export(out, format=fmt)
    return {
        "status": "ok",
        "output_path": out,
        "original_duration_ms": len(audio),
        "trimmed_duration_ms": len(trimmed),
        "start_ms": start_ms,
        "end_ms": end_ms,
    }


def _convert(file_path: str, output_path: str, **kwargs) -> dict:
    """Convert audio to a different format."""
    output_format = kwargs.get("output_format", "mp3")
    audio = _load_audio(file_path)
    if not output_path:
        base = os.path.splitext(file_path)[0]
        output_path = f"{base}.{output_format}"
    bitrate = kwargs.get("bitrate", "192k")
    audio.export(output_path, format=output_format, bitrate=bitrate)
    return {
        "status": "ok",
        "output_path": output_path,
        "format": output_format,
        "duration_ms": len(audio),
    }


def _volume(file_path: str, output_path: str, **kwargs) -> dict:
    """Adjust audio volume."""
    change_db = float(kwargs.get("change_db", 0))
    audio = _load_audio(file_path)
    adjusted = audio + change_db
    out = _auto_output(file_path, output_path, suffix="_vol")
    fmt = os.path.splitext(out)[1].lstrip(".").lower() or "mp3"
    adjusted.export(out, format=fmt)
    return {
        "status": "ok",
        "output_path": out,
        "change_db": change_db,
        "original_dbfs": round(audio.dBFS, 2) if audio.dBFS != float("-inf") else None,
        "new_dbfs": round(adjusted.dBFS, 2) if adjusted.dBFS != float("-inf") else None,
        "duration_ms": len(adjusted),
    }


def _merge(file_path: str, output_path: str, **kwargs) -> dict:
    """Merge multiple audio files into one."""
    file_paths = kwargs.get("file_paths", [])
    all_paths = [file_path] + list(file_paths)
    if len(all_paths) < 2:
        return {"status": "error", "message": "At least 2 files are needed for merge (file_path + file_paths list)"}

    combined = _load_audio(all_paths[0])
    for fp in all_paths[1:]:
        combined += _load_audio(fp)

    out = _auto_output(file_path, output_path, suffix="_merged")
    fmt = os.path.splitext(out)[1].lstrip(".").lower() or "mp3"
    combined.export(out, format=fmt)
    return {
        "status": "ok",
        "output_path": out,
        "merged_files": all_paths,
        "file_count": len(all_paths),
        "total_duration_ms": len(combined),
    }


_OPERATIONS = {
    "info": _info,
    "trim": _trim,
    "convert": _convert,
    "volume": _volume,
    "merge": _merge,
}


def run(operation: str, file_path: str, output_path: str = "", **kwargs) -> dict:
    """Process audio files.

    Parameters
    ----------
    operation : str
        One of: info, trim, convert, volume, merge.
    file_path : str
        Path to the input audio file.
    output_path : str
        Path for the output file (auto-generated if empty).
    **kwargs :
        start_ms, end_ms : int – for trim.
        output_format : str – for convert (default ``"mp3"``).
        bitrate : str – for convert (default ``"192k"``).
        change_db : float – for volume adjustment.
        file_paths : list[str] – additional files for merge.

    Returns
    -------
    dict with ``status`` and operation-specific data.
    """
    handler = _OPERATIONS.get(operation)
    if handler is None:
        return {
            "status": "error",
            "message": f"Unknown operation: {operation}. Valid operations: {', '.join(_OPERATIONS)}",
        }

    if not file_path:
        return {"status": "error", "message": "file_path is required"}

    if operation != "merge" and not os.path.isfile(file_path):
        return {"status": "error", "message": f"File not found: {file_path}"}

    try:
        return handler(file_path, output_path, **kwargs)
    except FileNotFoundError as exc:
        return {"status": "error", "message": f"File not found: {exc}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
