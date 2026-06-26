"""Persistent consent-grant store for credentialed MCPs (Stage 3B-1).

Stores ONLY consent metadata — NEVER a secret / API-key / token VALUE, host-env value,
full environment, or runtime output. A single JSON file at
``config_dir()/consent_grants.json``.

Fail-closed: corrupt JSON, unknown ``schema_version``, too-open permissions, a symlink, or
an invalid grant ⇒ ``GrantStoreError``. This module is pure metadata I/O — NO docker, NO
egress, NO secret reads, NO container starts.

Permissions: config dir ``0700``, file ``0600``. Permission-bit enforcement is POSIX-only
(``os.name == "posix"``); on Windows the store still works (atomic writes + schema + symlink
checks) but perm-bit checks are skipped (Windows ACLs differ).
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from pathlib import Path

from agentnode_sdk.config import config_dir

SCHEMA_VERSION = 1
_GRANTS_FILENAME = "consent_grants.json"

# Lifetime presets — chosen at consent time, never silently set. 90d is the DEFAULT;
# "forever" is an explicit advanced choice and is NEVER the default.
LIFETIME_THIS_RUN = "this_run"   # ephemeral: authorizes ONLY the current run, persists NO grant
LIFETIME_7D = "7d"
LIFETIME_30D = "30d"
LIFETIME_90D = "90d"
LIFETIME_FOREVER = "forever"     # advanced; expires_at = None (valid until revoked / identity change)
DEFAULT_LIFETIME = LIFETIME_90D
PERSISTED_LIFETIMES = (LIFETIME_7D, LIFETIME_30D, LIFETIME_90D, LIFETIME_FOREVER)
ALL_LIFETIMES = (LIFETIME_THIS_RUN,) + PERSISTED_LIFETIMES
_LIFETIME_SECONDS = {LIFETIME_7D: 7 * 86400, LIFETIME_30D: 30 * 86400, LIFETIME_90D: 90 * 86400}

# A grant MAY contain ONLY these fields. Anything else (or a secret-looking name) is refused.
_ALLOWED_GRANT_FIELDS = frozenset({
    "schema_version", "consent_key", "slug", "version", "artifact_hash",
    "env_key_names", "allowed_domains", "created_at", "expires_at", "lifetime",
    "revoked", "revoked_at",
})
# Defensive: a field NAME containing any of these would imply secret storage — refuse.
_FORBIDDEN_FIELD_SUBSTR = (
    "secret", "token", "api_key", "apikey", "password", "passwd",
    "credential", "env_value", "environ", "value",
)
_REQUIRED_GRANT_FIELDS = (
    "consent_key", "slug", "version", "artifact_hash",
    "env_key_names", "allowed_domains", "created_at", "lifetime",
)


class GrantStoreError(RuntimeError):
    """The grant store is unusable, insecure, or a grant is invalid (fail-closed)."""


def grants_path() -> Path:
    return config_dir() / _GRANTS_FILENAME


def _posix() -> bool:
    return os.name == "posix"


def _ensure_dir_secure(d: Path) -> None:
    """Create the config dir 0700 if missing; refuse a symlink; on POSIX refuse too-open perms."""
    if d.is_symlink():
        raise GrantStoreError(f"config dir is a symlink, refusing: {d}")
    if not d.exists():
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        return
    if not d.is_dir():
        raise GrantStoreError(f"config path is not a directory: {d}")
    if _posix():
        mode = stat.S_IMODE(d.stat().st_mode)
        if mode & 0o077:
            raise GrantStoreError(f"config dir permissions too open ({oct(mode)}); want 0700")


def _check_file_secure(f: Path) -> None:
    if f.is_symlink():
        raise GrantStoreError(f"grant store is a symlink, refusing: {f}")
    if _posix():
        mode = stat.S_IMODE(f.stat().st_mode)
        if mode & 0o077:
            raise GrantStoreError(f"grant store permissions too open ({oct(mode)}); want 0600")


def _validate_grant_shape(g) -> None:
    if not isinstance(g, dict):
        raise GrantStoreError("grant is not an object")
    extra = set(g) - _ALLOWED_GRANT_FIELDS
    if extra:
        raise GrantStoreError(f"grant has unexpected field(s): {sorted(extra)}")
    for k in g:
        if any(s in k.lower() for s in _FORBIDDEN_FIELD_SUBSTR):
            raise GrantStoreError(f"grant field name looks secret-bearing, refusing: {k!r}")
    for req in _REQUIRED_GRANT_FIELDS:
        if req not in g:
            raise GrantStoreError(f"grant missing required field: {req}")
    if not isinstance(g["consent_key"], str) or not g["consent_key"]:
        raise GrantStoreError("grant consent_key invalid")
    if g.get("lifetime") not in PERSISTED_LIFETIMES:
        raise GrantStoreError(f"grant lifetime invalid: {g.get('lifetime')!r}")
    if not isinstance(g.get("env_key_names"), list) or not isinstance(g.get("allowed_domains"), list):
        raise GrantStoreError("grant env_key_names/allowed_domains must be lists")


def load() -> list[dict]:
    """Return the validated list of stored grants. Missing file ⇒ ``[]``. Fail-closed on an
    insecure config dir, a (broken) symlink at the store path, corrupt JSON, unknown schema,
    too-open file perms, or an invalid grant."""
    # The config dir must be secure even when only READING: a too-open dir could let an
    # attacker-planted grant be read and authorized. Creating a missing dir 0700 is fine;
    # an existing-but-too-open / symlinked dir is fail-closed (via _ensure_dir_secure).
    _ensure_dir_secure(config_dir())
    p = grants_path()
    # A symlink at the store path — INCLUDING a broken one — must NOT be treated as "missing".
    if p.is_symlink():
        raise GrantStoreError(f"grant store is a symlink, refusing: {p}")
    if not p.exists():
        return []
    _check_file_secure(p)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as e:
        raise GrantStoreError(f"grant store unreadable/corrupt: {e}")
    if not isinstance(raw, dict):
        raise GrantStoreError("grant store root is not an object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise GrantStoreError(f"unknown grant store schema_version: {raw.get('schema_version')!r}")
    grants = raw.get("grants")
    if not isinstance(grants, list):
        raise GrantStoreError("grant store 'grants' is not a list")
    for g in grants:
        _validate_grant_shape(g)
    return grants


def _atomic_write(grants: list[dict]) -> None:
    d = config_dir()
    _ensure_dir_secure(d)
    p = grants_path()
    if p.is_symlink():
        raise GrantStoreError(f"grant store is a symlink, refusing: {p}")
    payload = json.dumps(
        {"schema_version": SCHEMA_VERSION, "grants": grants}, indent=2, sort_keys=True,
    )
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".consent_grants.", suffix=".tmp")
    try:
        if _posix():
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, p)  # atomic on POSIX + Windows
        if _posix():
            os.chmod(p, 0o600)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _is_active(g: dict, now: float) -> bool:
    if g.get("revoked"):
        return False
    exp = g.get("expires_at")
    if exp is not None:
        try:
            if float(exp) <= now:
                return False
        except (TypeError, ValueError):
            return False  # unparseable expiry ⇒ treat as inactive (fail-closed)
    return True


def find_valid(consent_key: str, now: float | None = None) -> dict | None:
    """Return an ACTIVE (not expired, not revoked) grant matching ``consent_key``, or None.
    Because ``consent_key`` binds slug+version+artifact_hash+env_key_names+allowed_domains, a
    match is by definition an exact-identity match."""
    if now is None:
        now = time.time()
    for g in load():
        if g.get("consent_key") == consent_key and _is_active(g, now):
            return g
    return None


def add(*, consent_key: str, slug: str, version: str, artifact_hash: str,
        env_key_names, allowed_domains, lifetime: str, now: float | None = None) -> dict:
    """Persist a grant (metadata only). ``this_run`` is ephemeral and MUST NOT be persisted
    (call sites authorize it without storing). Re-consent for the same ``consent_key`` replaces
    the prior grant. Returns the stored grant dict."""
    if now is None:
        now = time.time()
    if lifetime == LIFETIME_THIS_RUN:
        raise GrantStoreError("ephemeral 'this_run' consent must not be persisted")
    if lifetime not in PERSISTED_LIFETIMES:
        raise GrantStoreError(f"invalid lifetime: {lifetime!r}")
    expires_at = None if lifetime == LIFETIME_FOREVER else now + _LIFETIME_SECONDS[lifetime]
    grant = {
        "schema_version": SCHEMA_VERSION,
        "consent_key": consent_key,
        "slug": slug,
        "version": version,
        "artifact_hash": artifact_hash,
        "env_key_names": list(env_key_names),
        "allowed_domains": list(allowed_domains),
        "created_at": now,
        "expires_at": expires_at,
        "lifetime": lifetime,
        "revoked": False,
    }
    _validate_grant_shape(grant)
    grants = [g for g in load() if g.get("consent_key") != consent_key]
    grants.append(grant)
    _atomic_write(grants)
    return grant


def revoke(slug: str, key: str | None = None, now: float | None = None) -> int:
    """Mark grants for ``slug`` (optionally only ``consent_key == key``) revoked. Immediate.
    Returns the number revoked."""
    if now is None:
        now = time.time()
    grants = load()
    n = 0
    for g in grants:
        if (g.get("slug") == slug and (key is None or g.get("consent_key") == key)
                and not g.get("revoked")):
            g["revoked"] = True
            g["revoked_at"] = now
            n += 1
    if n:
        _atomic_write(grants)
    return n


def revoke_all(now: float | None = None) -> int:
    """Mark ALL grants revoked. Returns the number revoked."""
    if now is None:
        now = time.time()
    grants = load()
    n = 0
    for g in grants:
        if not g.get("revoked"):
            g["revoked"] = True
            g["revoked_at"] = now
            n += 1
    if n:
        _atomic_write(grants)
    return n


def list_grants() -> list[dict]:
    """Return grants for display. Contains ONLY metadata — there is no secret value to leak."""
    return load()
