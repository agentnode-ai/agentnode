"""Lockfile entry integrity — drift and manipulation detection.

Computes a per-entry SHA256 hash over canonical (immutable) fields.
Mutable fields (trust_level, installed_at, etc.) are excluded so that
legitimate runtime updates (TTL refresh) don't break integrity.

Phase 15.1: core module only — no CLI, no runtime integration, no audit.

Security note: This module detects WHETHER an entry changed, not WHO
changed it. Publisher/registry authentication is Phase 16+.

Known limitations:
- trust_level is mutable and excluded from the hash. Local manipulation
  of trust_level is NOT detected. Trust enforcement relies on policy/TTL.
- install_mode is mutable. If it gains runtime semantics, promote to canonical.
- Runtime verify can only report "mismatch", not which fields changed
  (only the hash is stored, not the original canonical payload).
  Field-level diffs are only possible at update time when both old and
  new entries are in memory.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


CANONICAL_VERSION = 1

CANONICAL_FIELDS = (
    "version",
    "package_type",
    "runtime",
    "entrypoint",
    "artifact_hash",
    "tools",
    "permissions",
    "mcp_command",
    "remote_endpoint",
    "connector",
    "agent",
    "prompts",
    "resources",
    "assets",
)

MUTABLE_FIELDS = (
    "installed_at",
    "last_trust_check",
    "trust_level",
    "source",
    "install_path",
    "install_mode",
    "capability_ids",
)

SENSITIVE_FIELDS: dict[str, str] = {
    "runtime": "Execution runtime changed",
    "entrypoint": "Code entrypoint changed",
    "mcp_command": "MCP process command changed",
    "remote_endpoint": "Remote endpoint changed",
    "package_type": "Package type changed",
}

PERMISSION_ESCALATIONS: dict[str, tuple[str, set[str]]] = {
    "network_level": ("none", {"restricted", "full"}),
    "filesystem_level": ("none", {"temp", "full"}),
    "code_execution_level": ("none", {"sandboxed", "full"}),
}


@dataclass
class IntegrityResult:
    """Result of verifying a lockfile entry's integrity."""

    status: str  # "verified", "missing", "mismatch"
    slug: str


@dataclass
class SensitiveChange:
    """A security-relevant change between two lockfile entries."""

    field: str
    old: Any
    new: Any
    description: str


def _build_canonical(entry: dict) -> dict:
    """Extract canonical fields from a lockfile entry.

    Missing fields are omitted (not set to null/empty) so that entries
    written before a field existed produce the same hash as entries
    where the field is absent.
    """
    canonical = {}
    for f in CANONICAL_FIELDS:
        if f in entry and entry[f] is not None:
            canonical[f] = entry[f]
    return canonical


def compute_integrity(entry: dict) -> dict:
    """Compute the ``_integrity`` dict for a lockfile entry."""
    canonical = _build_canonical(entry)
    payload = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "algorithm": "sha256",
        "canonical_version": CANONICAL_VERSION,
        "hash": digest,
    }


def seal_entry(entry: dict) -> dict:
    """Return a copy of *entry* with ``_integrity`` set.

    Idempotent: calling twice produces the same result.
    """
    sealed = dict(entry)
    sealed.pop("_integrity", None)
    sealed["_integrity"] = compute_integrity(sealed)
    return sealed


def verify_entry(slug: str, entry: dict) -> IntegrityResult:
    """Verify a single lockfile entry's integrity."""
    integrity = entry.get("_integrity")
    if integrity is None:
        return IntegrityResult(status="missing", slug=slug)

    stored_hash = integrity.get("hash", "")

    clean = dict(entry)
    clean.pop("_integrity", None)
    expected = compute_integrity(clean)

    if expected["hash"] == stored_hash:
        return IntegrityResult(status="verified", slug=slug)

    return IntegrityResult(status="mismatch", slug=slug)


def detect_sensitive_changes(
    old_entry: dict,
    new_entry: dict,
) -> list[SensitiveChange]:
    """Compare two entries and return security-relevant field changes.

    Only usable at update time when both entries are in memory.
    NOT usable for runtime verify (only hash is stored, not original values).
    """
    changes: list[SensitiveChange] = []

    for f, description in SENSITIVE_FIELDS.items():
        old_val = old_entry.get(f)
        new_val = new_entry.get(f)
        if old_val != new_val:
            changes.append(SensitiveChange(
                field=f, old=old_val, new=new_val, description=description,
            ))

    old_perms = old_entry.get("permissions") or {}
    new_perms = new_entry.get("permissions") or {}
    for perm, (safe, escalated) in PERMISSION_ESCALATIONS.items():
        old_val = old_perms.get(perm, safe)
        new_val = new_perms.get(perm, safe)
        if old_val == safe and new_val in escalated:
            changes.append(SensitiveChange(
                field=f"permissions.{perm}",
                old=old_val,
                new=new_val,
                description=f"Permission escalation: {perm}",
            ))

    return changes
