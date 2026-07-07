"""Credentialed toolpacks — declared ``env_requirements`` at run time.

A toolpack may declare the credentials it needs in its manifest::

    env_requirements:
      - name: AHREFS_API_KEY
        required: true
        description: Ahrefs API key

The declaration is sealed into the lockfile at install (integrity-covered).
This module is PURE of secret values: it only ever handles env-var NAMES and
checks presence (``name in os.environ``) — a value is never read here.
"""

from __future__ import annotations

import os


def declared_env_names(entry: dict | None) -> list[str]:
    """All declared credential NAMES (required + optional), sorted + de-duped."""
    reqs = (entry or {}).get("env_requirements") or []
    names = set()
    for r in reqs:
        if isinstance(r, dict) and r.get("name"):
            names.add(str(r["name"]))
    return sorted(names)


def required_env_names(entry: dict | None) -> list[str]:
    """Declared NAMES with ``required`` truthy (missing flag = required)."""
    reqs = (entry or {}).get("env_requirements") or []
    names = set()
    for r in reqs:
        if isinstance(r, dict) and r.get("name") and r.get("required", True):
            names.add(str(r["name"]))
    return sorted(names)


def missing_required_env(entry: dict | None) -> list[str]:
    """Required NAMES not present in the host environment (presence only —
    no value is read)."""
    return [n for n in required_env_names(entry) if n not in os.environ]


def missing_env_message(slug: str, missing: list[str]) -> str:
    """Actionable, value-free error for a run blocked on missing credentials."""
    keys = ", ".join(missing)
    plural = "s" if len(missing) != 1 else ""
    return (
        f"Toolpack '{slug}' requires the environment variable{plural} {keys} "
        f"(declared in its env_requirements). Set {'them' if plural else 'it'} "
        "in your environment and retry."
    )
