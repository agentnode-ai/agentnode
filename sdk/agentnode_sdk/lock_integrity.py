"""Lockfile entry integrity — drift and manipulation detection.

Computes a per-entry SHA256 hash over canonical (immutable) fields.
Mutable fields (trust_level, installed_at, etc.) are excluded so that
legitimate runtime updates (TTL refresh) don't break integrity.

Security note: This module detects WHETHER an entry changed, not WHO
changed it. Publisher/registry authentication is in ``signature.py``.

canonical_version history:
- v1: 15 canonical fields (Phase 15). No _signatures awareness.
- v2: v1 fields + _signatures (Phase 16). Detects signature/key swap.
- v3: v2 fields + publisher_slug (Phase 16.6). Publisher identity integrity.

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


CANONICAL_VERSION = 3

CANONICAL_FIELDS = (
    "version",
    "package_type",
    "runtime",
    "entrypoint",
    "artifact_hash",
    "tools",
    "permissions",
    "mcp_command",
    "mcp_env_keys",
    "remote_endpoint",
    "connector",
    "agent",
    "prompts",
    "resources",
    "assets",
    # P0.3: how a community toolpack was isolated at install. Sealed so a tampered
    # lockfile (flip sandboxed, repoint the volume) breaks integrity; the run-time
    # volume gate (recompute name from slug+version+hash) is the second layer.
    "sandboxed",
    "sandbox_volume",
)

CANONICAL_FIELDS_V2 = CANONICAL_FIELDS + ("_signatures",)

CANONICAL_FIELDS_V3 = CANONICAL_FIELDS_V2 + ("publisher_slug",)

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
    "mcp_env_keys": "MCP environment key declarations changed",
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


def _build_canonical(entry: dict, canonical_version: int = 1) -> dict:
    """Extract canonical fields from a lockfile entry.

    Missing fields are omitted (not set to null/empty) so that entries
    written before a field existed produce the same hash as entries
    where the field is absent.

    canonical_version=1: Phase 15 fields (no _signatures).
    canonical_version=2: Phase 15 fields + _signatures.
    canonical_version=3: Phase 16 fields + publisher_slug.
    """
    if canonical_version >= 3:
        fields = CANONICAL_FIELDS_V3
    elif canonical_version >= 2:
        fields = CANONICAL_FIELDS_V2
    else:
        fields = CANONICAL_FIELDS
    canonical = {}
    for f in fields:
        if f in entry and entry[f] is not None:
            canonical[f] = entry[f]
    return canonical


def _detect_canonical_version(entry: dict) -> int:
    """Auto-detect canonical version from entry content."""
    pub = entry.get("publisher_slug")
    if isinstance(pub, str) and pub.strip():
        return 3
    sigs = entry.get("_signatures")
    if isinstance(sigs, dict) and len(sigs) > 0:
        return 2
    return 1


def compute_integrity(entry: dict, *, canonical_version: int | None = None) -> dict:
    """Compute the ``_integrity`` dict for a lockfile entry.

    If *canonical_version* is ``None``, auto-detect from entry content:
    entries with ``_signatures`` get v2, others get v1.
    """
    if canonical_version is None:
        canonical_version = _detect_canonical_version(entry)
    canonical = _build_canonical(entry, canonical_version=canonical_version)
    payload = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "algorithm": "sha256",
        "canonical_version": canonical_version,
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
    """Verify a single lockfile entry's integrity.

    Uses the stored ``canonical_version`` to pick the field list,
    so v1 entries are verified against v1 fields and v2 entries
    against v2 fields.
    """
    integrity = entry.get("_integrity")
    if integrity is None:
        return IntegrityResult(status="missing", slug=slug)

    stored_hash = integrity.get("hash", "")
    stored_version = integrity.get("canonical_version", 1)

    clean = dict(entry)
    clean.pop("_integrity", None)
    expected = compute_integrity(clean, canonical_version=stored_version)

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
