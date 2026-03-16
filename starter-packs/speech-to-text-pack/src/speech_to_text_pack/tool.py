"""Speech-to-text transcription via local Whisper model or OpenAI Whisper API."""

from __future__ import annotations

import os


def run(
    audio_path: str,
    model: str = "base",
    language: str | None = None,
    api_key: str = "",
) -> dict:
    """Transcribe an audio file to text.

    Args:
        audio_path: Path to the audio file.
        model: Whisper model name for local inference (tiny, base, small, medium, large).
        language: Language code (e.g. "en"). None for auto-detection.
        api_key: If provided, use the OpenAI Whisper API instead of local model.

    Returns:
        dict with text, language, duration, segments.
    """
    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if api_key:
        return _transcribe_api(audio_path, language, api_key)
    else:
        return _transcribe_local(audio_path, model, language)


def _transcribe_api(audio_path: str, language: str | None, api_key: str) -> dict:
    """Transcribe using OpenAI Whisper API via httpx."""
    import httpx

    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}

    with open(audio_path, "rb") as f:
        files = {"file": (os.path.basename(audio_path), f, "audio/mpeg")}
        data: dict = {"model": "whisper-1", "response_format": "verbose_json"}
        if language:
            data["language"] = language

        with httpx.Client(timeout=300.0) as client:
            resp = client.post(url, headers=headers, files=files, data=data)
            resp.raise_for_status()
            result = resp.json()

    segments = []
    for seg in result.get("segments", []):
        segments.append({
            "id": seg.get("id", 0),
            "start": seg.get("start", 0.0),
            "end": seg.get("end", 0.0),
            "text": seg.get("text", ""),
        })

    return {
        "text": result.get("text", ""),
        "language": result.get("language", language or "unknown"),
        "duration": result.get("duration", 0.0),
        "segments": segments,
    }


def _transcribe_local(audio_path: str, model: str, language: str | None) -> dict:
    """Transcribe using local Whisper model."""
    import whisper

    whisper_model = whisper.load_model(model)

    options: dict = {}
    if language:
        options["language"] = language

    result = whisper_model.transcribe(audio_path, **options)

    segments = []
    for seg in result.get("segments", []):
        segments.append({
            "id": seg.get("id", 0),
            "start": seg.get("start", 0.0),
            "end": seg.get("end", 0.0),
            "text": seg.get("text", ""),
        })

    # Estimate duration from last segment end time
    duration = 0.0
    if segments:
        duration = segments[-1]["end"]

    detected_language = result.get("language", language or "unknown")

    return {
        "text": result.get("text", ""),
        "language": detected_language,
        "duration": duration,
        "segments": segments,
    }
