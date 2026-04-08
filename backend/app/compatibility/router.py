"""Compatibility matrix endpoint — GET /v1/compatibility"""

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Response

from app.shared.exceptions import AppError
from app.shared.rate_limit import rate_limit

router = APIRouter(prefix="/v1/compatibility", tags=["compatibility"])

_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "compatibility_matrix.json"

# In-memory cache with mtime-based invalidation
_cache: dict[str, Any] = {}
_cache_mtime: float = 0.0


def _load_data() -> dict[str, Any]:
    """Load compatibility data, using mtime-based cache invalidation."""
    global _cache, _cache_mtime

    if not _DATA_PATH.exists():
        raise AppError(
            "COMPATIBILITY_UNAVAILABLE",
            "Compatibility data not available. Data file missing.",
            503,
        )

    try:
        current_mtime = os.path.getmtime(_DATA_PATH)
    except OSError:
        raise AppError(
            "COMPATIBILITY_UNAVAILABLE",
            "Compatibility data not available. Cannot stat file.",
            503,
        )

    if current_mtime != _cache_mtime or not _cache:
        try:
            with open(_DATA_PATH, encoding="utf-8") as f:
                _cache = json.load(f)
            _cache_mtime = current_mtime
        except (json.JSONDecodeError, OSError) as exc:
            raise AppError(
                "COMPATIBILITY_UNAVAILABLE",
                f"Compatibility data corrupted: {exc}",
                503,
            )

    return _cache


@router.get("", dependencies=[Depends(rate_limit(30, 60))])
async def get_compatibility(response: Response):
    """Full compatibility matrix across all providers."""
    data = _load_data()
    response.headers["Cache-Control"] = "public, max-age=3600"
    return data


@router.get("/{provider}", dependencies=[Depends(rate_limit(30, 60))])
async def get_provider_compatibility(provider: str, response: Response):
    """Compatibility data for a single provider."""
    data = _load_data()
    provider_lower = provider.lower()

    for p in data.get("providers", []):
        if p["name"] == provider_lower:
            response.headers["Cache-Control"] = "public, max-age=3600"
            return {
                "generated_at": data.get("generated_at"),
                "source_version": data.get("source_version"),
                "provider": p["name"],
                "models": p["models"],
                "model_count": len(p["models"]),
            }

    raise AppError(
        "PROVIDER_NOT_FOUND",
        f"Provider '{provider_lower}' not found in compatibility data.",
        404,
    )
