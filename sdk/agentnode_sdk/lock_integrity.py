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
import re
from dataclasses import dataclass
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
    # Stage 4A: MCP pre-install into a sealed volume. Appended at the end so entries
    # WITHOUT these fields hash identically to pre-Stage-4 (backward-compatible —
    # _build_canonical omits absent fields). mcp_command is NOT changed by 4A.
    "mcp_preinstalled",
    "mcp_preinstall",
    "mcp_sandbox_volume",
    "mcp_preinstall_command",
    # Stage 5: the publisher-attested, canonicalized egress allowlist, sealed at install
    # so a future Stage 3B trusts a tamper-evident value (NOT consumed at run time yet).
    "mcp_allowed_domains",
    # Credentialed toolpacks: declared env-var NAMES ({name, required, description}),
    # sealed so a tampered lockfile cannot widen or swap the credential set. Appended
    # at the end — entries WITHOUT the field hash identically (backward-compatible).
    "env_requirements",
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
    "mcp_preinstall": "MCP preinstall descriptor changed",
    "mcp_sandbox_volume": "MCP sandbox volume changed",
    "mcp_preinstalled": "MCP preinstall status changed",
    "mcp_preinstall_command": "MCP preinstall command changed",
    "mcp_allowed_domains": "MCP egress allowlist changed",
    "env_requirements": "Declared credential requirements changed",
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


# ===========================================================================
# Structure digest (Slice 0.2A-1b) — unkeyed global drift/reseal detection.
#
# The structure digest binds the SET of (slug -> per-entry _integrity). It is
# NOT a signature and NOT an authenticity attestation: an attacker who can edit
# the lockfile can recompute it. Its only job is to detect entry addition /
# removal / transplant (and non-reseal drift) that per-entry integrity misses.
# Hash-of-hashes: it changes only when a slug or an entry's _integrity changes,
# so a content change without re-sealing the entry leaves the structure verified
# (the per-entry hash is the authority on entry content).
#
# This slice is CORE LOGIC ONLY — no run_tool/installer/CLI integration, no
# automatic reseal. Serialization mirrors compute_integrity exactly.
# ===========================================================================

STRUCTURE_KIND = "agentnode.lock.structure"
STRUCTURE_CANONICALIZATION_VERSION = 1
_SUPPORTED_STRUCTURE_VERSIONS = (1,)
_HEX64 = re.compile(r"[0-9a-f]{64}")

_slug_re_cache = None


def _slug_valid(slug: Any) -> bool:
    """Reuse the single central ASCII-kebab slug rule (``cli.init.SLUG_RE``).

    No new regex, no normalization. Non-string keys are rejected. Imported
    lazily + cached to avoid a module-load dependency from core into the CLI.
    """
    global _slug_re_cache
    if _slug_re_cache is None:
        from agentnode_sdk.cli.init import SLUG_RE
        _slug_re_cache = SLUG_RE
    return isinstance(slug, str) and bool(_slug_re_cache.match(slug))


class StructureIntegrityError(Exception):
    """The lockfile cannot be reduced to a canonical structure input.

    Raised by :func:`compute_structure_digest` / :func:`seal_structure` when the
    base model is invalid or an entry lacks a well-formed ``_integrity`` (so seal
    REFUSES an invalid/unsealed set). :func:`verify_structure` catches it and
    reports ``"invalid"`` instead of raising.
    """


def _canonical_integrity(integ: Any) -> dict:
    """Return the exact, normalised ``_integrity`` triple, or raise.

    Requires a well-formed object with ``algorithm`` == ``"sha256"``, an integer
    ``canonical_version`` >= 1, and a 64-char lowercase hex ``hash``. Unknown
    extra fields are dropped (not part of the canonicalization). Never maps to
    ``None``.
    """
    if not isinstance(integ, dict):
        raise StructureIntegrityError("_integrity is missing or not an object")
    algo = integ.get("algorithm")
    cver = integ.get("canonical_version")
    h = integ.get("hash")
    if algo != "sha256":
        raise StructureIntegrityError("_integrity.algorithm must be 'sha256'")
    # Reuse the existing per-entry version ceiling (CANONICAL_VERSION) — do not
    # invent a separate ">= 1" rule. An unsupported version is structure_invalid.
    if not (isinstance(cver, int) and not isinstance(cver, bool) and 1 <= cver <= CANONICAL_VERSION):
        raise StructureIntegrityError("_integrity.canonical_version is unsupported")
    if not (isinstance(h, str) and _HEX64.fullmatch(h)):
        raise StructureIntegrityError("_integrity.hash is not 64 lowercase hex chars")
    return {"algorithm": "sha256", "canonical_version": cver, "hash": h}


def _canonical_structure_input(lock: dict, canonicalization_version: int) -> dict:
    """Build the canonical structure input, or raise StructureIntegrityError.

    Validates the base model (dict, string ``lockfile_version``, dict
    ``packages``) and every entry's ``_integrity``. Excludes ``structure_digest``
    and ``updated_at`` by construction. Entries are sorted by the raw slug (no
    NFC / no re-normalization — slugs are ASCII kebab-case).
    """
    if not isinstance(lock, dict):
        raise StructureIntegrityError("lockfile is not an object")
    lockfile_version = lock.get("lockfile_version")
    if not isinstance(lockfile_version, str):
        raise StructureIntegrityError("lockfile_version is missing or not a string")
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise StructureIntegrityError("packages is missing or not an object")

    entries: list = []
    for slug, entry in packages.items():
        if not _slug_valid(slug):
            raise StructureIntegrityError("invalid package slug (not ASCII kebab-case)")
        if not isinstance(entry, dict):
            raise StructureIntegrityError(f"entry {slug!r} is not an object")
        entries.append([slug, _canonical_integrity(entry.get("_integrity"))])
    entries.sort(key=lambda pair: pair[0])  # raw ASCII slug order — deterministic

    return {
        "kind": STRUCTURE_KIND,
        "lockfile_version": lockfile_version,
        "canonicalization_version": canonicalization_version,
        "entries": entries,
    }


def compute_structure_digest(
    lock: dict, *, canonicalization_version: int = STRUCTURE_CANONICALIZATION_VERSION
) -> str:
    """Compute the lowercase-hex SHA-256 structure digest for *lock*.

    Raises :class:`StructureIntegrityError` if the base model is invalid or any
    entry lacks a well-formed ``_integrity``. Order-independent (entries sorted
    by slug); an empty ``packages`` still yields a deterministic digest.
    """
    canonical = _canonical_structure_input(lock, canonicalization_version)
    payload = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_structure(lock: dict) -> str:
    """Return the structure status: ``verified|missing|mismatch|unsupported|invalid``.

    - ``missing``: no ``structure_digest`` field.
    - ``unsupported``: the stored ``canonicalization_version`` is unknown.
    - ``invalid``: malformed ``structure_digest`` field, invalid base model, or
      invalid entry ``_integrity``.
    - ``mismatch``: a formally-valid stored digest disagrees with the recompute.

    Never mutates *lock*. Does not decide CLI exit codes or runtime results.

    Order of checks (deliberate): a non-dict argument, an invalid base model or
    any invalid entry integrity resolve to ``invalid`` BEFORE the missing check —
    a broken lockfile without a digest is not a mere migration case. Only a truly
    absent ``structure_digest`` key is ``missing``; an explicit ``null`` (or any
    other malformed value) is ``invalid``.
    """
    if not isinstance(lock, dict):
        return "invalid"
    # Validate the base model + every entry integrity first (raises → invalid).
    # Reused for the recompute below, so this is computed once.
    try:
        base_digest = compute_structure_digest(lock)
    except StructureIntegrityError:
        return "invalid"

    if "structure_digest" not in lock:
        return "missing"
    sd = lock["structure_digest"]          # present: null or malformed → invalid
    if not isinstance(sd, dict):
        return "invalid"
    algo = sd.get("algorithm")
    cver = sd.get("canonicalization_version")
    stored = sd.get("hash")
    if algo != "sha256":
        return "invalid"
    if not isinstance(cver, int) or isinstance(cver, bool):
        return "invalid"
    if cver not in _SUPPORTED_STRUCTURE_VERSIONS:
        return "unsupported"
    if not (isinstance(stored, str) and _HEX64.fullmatch(stored)):
        return "invalid"
    recomputed = (
        base_digest
        if cver == STRUCTURE_CANONICALIZATION_VERSION
        else compute_structure_digest(lock, canonicalization_version=cver)
    )
    return "verified" if recomputed == stored else "mismatch"


def seal_structure(lock: dict) -> dict:
    """Return a COPY of *lock* with ``structure_digest`` set/replaced.

    Does NOT mutate the input. Only the top-level ``structure_digest`` is written;
    entries, per-entry ``_integrity`` and ``updated_at`` are untouched. Refuses
    (raises :class:`StructureIntegrityError`) an invalid or unsealed entry set —
    the per-entry seal must run first. Deterministic and idempotent. Deliberately
    re-sealing a formally-valid but divergent digest is allowed here as a pure
    core operation; the ``--force`` policy lives in the later integration slice.
    """
    digest = compute_structure_digest(lock)
    sealed = dict(lock)
    sealed["structure_digest"] = {
        "algorithm": "sha256",
        "canonicalization_version": STRUCTURE_CANONICALIZATION_VERSION,
        "hash": digest,
    }
    return sealed


# ---------------------------------------------------------------------------
# Runtime-neutral integrity report + decision (no RunToolResult / CLI types).
#
# The allow decision follows the agreed matrix. Per-entry STATUS is delegated to
# the existing verify_entry (no re-implementation); only the combined allow rule
# lives here. No surface logs or audits this — that is the integration slice.
# ---------------------------------------------------------------------------

@dataclass
class LockIntegrityReport:
    """Runtime-neutral integrity decision for one execution of *slug*."""

    entry_status: str        # verified | missing | mismatch | absent
    structure_status: str    # verified | missing | mismatch | unsupported | invalid
    strict: bool
    allowed: bool
    reason: str              # fixed, content-free (status names only; no values)


class LockIntegrityDenied(Exception):
    """Runtime-neutral denial. Carries the :class:`LockIntegrityReport`.

    Surfaces (run_tool, CLI, a future agent entrypoint) translate this into their
    own error type — this class depends on no runtime/CLI type.
    """

    def __init__(self, report: LockIntegrityReport):
        self.report = report
        super().__init__(report.reason)


def _entry_allowed(entry_status: str, strict: bool) -> bool:
    # 'absent' (the slug is not in packages, or is malformed) is NEVER runnable —
    # deny in both modes. 'missing' (entry present, no _integrity) keeps today's
    # migration semantics (continue). 'mismatch' is warn (normal) / deny (strict).
    if entry_status == "absent":
        return False
    if entry_status in ("verified", "missing"):
        return True
    return not strict


def _structure_allowed(structure_status: str, strict: bool) -> bool:
    # 0.2A matrix: only 'verified' is unconditionally allowed. Every other status
    # (incl. 'missing') is warn in normal mode and DENY in strict — deleting the
    # global field must not neutralise the check.
    if structure_status == "verified":
        return True
    return not strict


def evaluate_lock_integrity(slug: str, lock: dict, *, strict: bool) -> LockIntegrityReport:
    """Compute the combined per-entry + structure integrity report for *slug*.

    Entry status is delegated to :func:`verify_entry`; structure status to
    :func:`verify_structure`. Pure and runtime-neutral — a non-dict *lock* or
    ``packages`` yields a determined report (never an AttributeError). A slug that
    is not present in ``packages`` (or maps to a non-object) is ``absent`` and is
    denied in both modes — distinct from an existing entry whose ``_integrity`` is
    merely ``missing``.
    """
    structure_status = verify_structure(lock)
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict) or not isinstance(packages.get(slug), dict):
        entry_status = "absent"
    else:
        entry_status = verify_entry(slug, packages[slug]).status

    e_ok = _entry_allowed(entry_status, strict)
    s_ok = _structure_allowed(structure_status, strict)
    allowed = e_ok and s_ok

    if entry_status == "verified" and structure_status == "verified":
        reason = "verified"
    else:
        flags = []
        if not e_ok:
            flags.append(f"entry_{entry_status}")
        if not s_ok:
            flags.append(f"structure_{structure_status}")
        if not flags:  # allowed, but at least one non-verified status → warn
            if entry_status != "verified":
                flags.append(f"entry_{entry_status}")
            if structure_status != "verified":
                flags.append(f"structure_{structure_status}")
        reason = ("denied: " if not allowed else "warn: ") + ",".join(flags)

    return LockIntegrityReport(
        entry_status=entry_status,
        structure_status=structure_status,
        strict=strict,
        allowed=allowed,
        reason=reason,
    )


def enforce_lock_integrity(slug: str, lock: dict, *, strict: bool) -> None:
    """Raise :class:`LockIntegrityDenied` if the combined decision denies *slug*.

    Returns ``None`` when allowed. Runtime-neutral — callers translate the
    exception into their own surface error.
    """
    report = evaluate_lock_integrity(slug, lock, strict=strict)
    if not report.allowed:
        raise LockIntegrityDenied(report)
