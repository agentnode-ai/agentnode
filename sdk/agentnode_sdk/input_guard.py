"""Input validation for tool arguments.

Checks kwargs passed to run_tool() for patterns that indicate
potential misuse. Returns InputFinding objects with severity levels:
- "warning": logged but non-blocking
- "prompt": requires confirmation (interactive) or denies (non-interactive)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

MAX_STRING_BYTES = 1_000_000  # 1 MB
MAX_COLLECTION_SIZE = 10_000

_PATH_TRAVERSAL_RE = re.compile(r"(^|[\\/])\.\.[\\/]")
_URL_RE = re.compile(r"https?://", re.IGNORECASE)


@dataclass
class InputFinding:
    level: str       # "warning" or "prompt"
    message: str
    code: str        # "path_traversal", "url_anomaly", "oversized_string", "oversized_collection"

    def __str__(self) -> str:
        return self.message


def validate_tool_input(
    slug: str,
    tool_name: str | None,
    kwargs: dict[str, Any],
    entry: dict,
) -> list[InputFinding]:
    """Validate tool arguments against known risk patterns.

    Returns list of InputFinding objects (empty = all clear).
    Each finding has a level ("warning" or "prompt") and a code.
    """
    if not kwargs:
        return []

    findings: list[InputFinding] = []
    perms = entry.get("permissions") or {}

    for key, value in kwargs.items():
        if isinstance(value, str):
            findings.extend(_check_string(key, value, perms))
        elif isinstance(value, (list, tuple, set, frozenset)):
            if len(value) > MAX_COLLECTION_SIZE:
                findings.append(InputFinding(
                    level="warning",
                    message=f"arg '{key}': collection has {len(value)} items (limit {MAX_COLLECTION_SIZE})",
                    code="oversized_collection",
                ))
        elif isinstance(value, dict):
            if len(value) > MAX_COLLECTION_SIZE:
                findings.append(InputFinding(
                    level="warning",
                    message=f"arg '{key}': dict has {len(value)} keys (limit {MAX_COLLECTION_SIZE})",
                    code="oversized_collection",
                ))

    return findings


def _check_string(key: str, value: str, perms: dict) -> list[InputFinding]:
    """Check a single string argument."""
    findings: list[InputFinding] = []

    if len(value.encode("utf-8", errors="replace")) > MAX_STRING_BYTES:
        findings.append(InputFinding(
            level="warning",
            message=f"arg '{key}': string exceeds {MAX_STRING_BYTES // 1_000_000} MB",
            code="oversized_string",
        ))

    if _PATH_TRAVERSAL_RE.search(value):
        findings.append(InputFinding(
            level="prompt",
            message=f"arg '{key}': contains path traversal pattern (..)",
            code="path_traversal",
        ))

    net_level = perms.get("network_level", "none")
    if net_level == "none" and _URL_RE.search(value):
        findings.append(InputFinding(
            level="prompt",
            message=f"arg '{key}': contains URL but package declares network_level=none",
            code="url_anomaly",
        ))

    return findings
