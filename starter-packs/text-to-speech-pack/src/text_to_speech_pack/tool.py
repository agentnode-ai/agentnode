"""Text-to-speech synthesis via OpenAI TTS API."""

from __future__ import annotations

import os
import time


def run(
    text: str,
    api_key: str,
    provider: str = "openai",
    voice: str = "alloy",
    output_path: str = "",
) -> dict:
    """Convert text to speech and save as an audio file.

    Args:
        text: The text to synthesise.
        api_key: API key for the provider.
        provider: TTS provider (currently "openai").
        voice: Voice name. OpenAI voices: alloy, echo, fable, onyx, nova, shimmer.
        output_path: File path to save audio. Auto-generated if empty.

    Returns:
        dict with output_path, voice, provider, duration_estimate.
    """
    import httpx

    if not api_key:
        raise ValueError("api_key is required")

    if provider != "openai":
        raise ValueError(f"Unsupported provider: {provider}. Currently only 'openai' is supported.")

    if not output_path:
        output_path = f"speech_{int(time.time())}.mp3"

    url = "https://api.openai.com/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "tts-1",
        "input": text,
        "voice": voice,
        "response_format": "mp3",
    }

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        audio_bytes = resp.content

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    # Estimate duration: roughly 150 words per minute for TTS
    word_count = len(text.split())
    duration_estimate = round((word_count / 150.0) * 60.0, 2)

    return {
        "output_path": os.path.abspath(output_path),
        "voice": voice,
        "provider": provider,
        "duration_estimate": duration_estimate,
    }
