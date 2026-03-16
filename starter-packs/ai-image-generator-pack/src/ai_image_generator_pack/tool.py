"""AI image generation via Stability AI or Replicate REST APIs."""

from __future__ import annotations

import io
import os
import time
import base64


def run(
    prompt: str,
    provider: str = "stability",
    api_key: str = "",
    output_path: str = "",
    width: int = 512,
    height: int = 512,
) -> dict:
    """Generate an image from a text prompt.

    Args:
        prompt: Text description of the image to generate.
        provider: API provider - "stability" or "replicate".
        api_key: API key for the chosen provider.
        output_path: File path to save the image. Auto-generated if empty.
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        dict with output_path, provider, prompt, width, height.
    """
    import httpx

    if not api_key:
        raise ValueError("api_key is required for image generation")

    if not output_path:
        output_path = f"generated_{int(time.time())}.png"

    if provider == "stability":
        image_bytes = _generate_stability(prompt, api_key, width, height)
    elif provider == "replicate":
        image_bytes = _generate_replicate(prompt, api_key, width, height)
    else:
        raise ValueError(f"Unsupported provider: {provider}. Use 'stability' or 'replicate'.")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(image_bytes)

    return {
        "output_path": os.path.abspath(output_path),
        "provider": provider,
        "prompt": prompt,
        "width": width,
        "height": height,
    }


def _generate_stability(prompt: str, api_key: str, width: int, height: int) -> bytes:
    """Generate image using Stability AI REST API."""
    import httpx

    url = "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "text_prompts": [{"text": prompt, "weight": 1.0}],
        "cfg_scale": 7,
        "width": width,
        "height": height,
        "steps": 30,
        "samples": 1,
    }

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    artifacts = data.get("artifacts", [])
    if not artifacts:
        raise RuntimeError("No image returned from Stability AI")

    return base64.b64decode(artifacts[0]["base64"])


def _generate_replicate(prompt: str, api_key: str, width: int, height: int) -> bytes:
    """Generate image using Replicate REST API."""
    import httpx

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }

    # Use the Replicate predictions API with SDXL
    create_url = "https://api.replicate.com/v1/predictions"
    payload = {
        "version": "39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b",
        "input": {
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_outputs": 1,
        },
    }

    with httpx.Client(timeout=300.0) as client:
        resp = client.post(create_url, json=payload, headers=headers)
        resp.raise_for_status()
        prediction = resp.json()

        # Poll until completed if not using Prefer: wait
        poll_url = prediction.get("urls", {}).get("get", "")
        status = prediction.get("status", "")

        while status not in ("succeeded", "failed", "canceled"):
            import time as _time
            _time.sleep(2)
            poll_resp = client.get(poll_url, headers=headers)
            poll_resp.raise_for_status()
            prediction = poll_resp.json()
            status = prediction.get("status", "")

        if status != "succeeded":
            raise RuntimeError(f"Replicate prediction {status}: {prediction.get('error', 'unknown error')}")

        output = prediction.get("output", [])
        if not output:
            raise RuntimeError("No image URL returned from Replicate")

        image_url = output[0] if isinstance(output, list) else output
        img_resp = client.get(image_url)
        img_resp.raise_for_status()
        return img_resp.content
