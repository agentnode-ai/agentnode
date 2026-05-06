"""Input validation for tool arguments — warning layer only.

Checks kwargs passed to run_tool() for patterns that indicate
potential misuse. Returns warning strings; never blocks execution.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

MAX_STRING_BYTES = 1_000_000  # 1 MB
MAX_COLLECTION_SIZE = 10_000

_PATH_TRAVERSAL_RE = re.compile(r"(^|[\\/])\.\.[\\/]")
_URL_RE = re.compile(r"https?://", re.IGNORECASE)


def validate_tool_input(
    slug: str,
    tool_name: str | None,
    kwargs: dict[str, Any],
    entry: dict,
) -> list[str]:
    """Validate tool arguments against known risk patterns.

    Returns list of warning strings (empty = all clear).
    """
    if not kwargs:
        return []

    warnings: list[str] = []
    perms = entry.get("permissions") or {}

    for key, value in kwargs.items():
        if isinstance(value, str):
            warnings.extend(_check_string(key, value, perms))
        elif isinstance(value, (list, tuple, set, frozenset)):
            if len(value) > MAX_COLLECTION_SIZE:
                warnings.append(
                    f"arg '{key}': collection has {len(value)} items (limit {MAX_COLLECTION_SIZE})"
                )
        elif isinstance(value, dict):
            if len(value) > MAX_COLLECTION_SIZE:
                warnings.append(
                    f"arg '{key}': dict has {len(value)} keys (limit {MAX_COLLECTION_SIZE})"
                )

    return warnings


def _check_string(key: str, value: str, perms: dict) -> list[str]:
    """Check a single string argument."""
    warnings: list[str] = []

    if len(value.encode("utf-8", errors="replace")) > MAX_STRING_BYTES:
        warnings.append(
            f"arg '{key}': string exceeds {MAX_STRING_BYTES // 1_000_000} MB"
        )

    if _PATH_TRAVERSAL_RE.search(value):
        warnings.append(f"arg '{key}': contains path traversal pattern (..)")

    net_level = perms.get("network_level", "none")
    if net_level == "none" and _URL_RE.search(value):
        warnings.append(
            f"arg '{key}': contains URL but package declares network_level=none"
        )

    return warnings
