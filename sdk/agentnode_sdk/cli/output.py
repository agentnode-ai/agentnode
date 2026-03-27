"""Output helpers for AgentNode CLI. All functions return str."""
from __future__ import annotations

import os
import sys

_color_override: bool | None = None


def _colors_enabled() -> bool:
    if _color_override is not None:
        return _color_override
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    return True


def set_color(enabled: bool) -> None:
    global _color_override
    _color_override = enabled


def bold(text: str) -> str:
    if _colors_enabled():
        return f"\033[1m{text}\033[0m"
    return text


def dim(text: str) -> str:
    if _colors_enabled():
        return f"\033[2m{text}\033[0m"
    return text


def section(title: str) -> str:
    line = bold(title)
    underline = "=" * len(title)
    return f"{line}\n{underline}\n"


def kv(label: str, value: str, width: int = 22) -> str:
    return f"  {label:<{width}}{value}"


def spacer() -> str:
    return ""
