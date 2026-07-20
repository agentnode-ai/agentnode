"""Import-free wheel build + distribution-identity validation (Agent-Exec A1-M1).

This module turns a verified agent *source* tree into a locally built, import-free
*validated* wheel and extracts the OBSERVED distribution identity (pip distribution
name + version) plus the import top-levels the wheel actually provides.

Trust boundary — read this before extending:

* The distribution identity produced here is **locally observed and locally sealed**.
  It is NOT publisher-signed and NOT a deterministic function of the source: a
  PEP-517 backend may emit arbitrary metadata. The publisher-authenticated facts
  (source artifact, ``artifact_hash``, existing signed fields) are unchanged and are
  verified *before* any build code runs (installer.py).
* The wheel is validated **as a ZIP only** — no module import, no code execution of
  the built artifact. A *cooperative* build produces a normal wheel; a *malicious*
  PEP-517 backend is host-capable code and can still cause host side effects. M1 does
  not defend against a malicious, already-host-accepted (trusted-tier) build backend
  — see the build-boundary note in ``build_wheel``.

Initial supported layouts (fail-closed on anything else — §6):
* a regular package with ``__init__.py``,
* a regular top-level ``.py`` module,
* pure-Python modules in the normal wheel layout.

Fail-closed for: namespace packages (PEP 420), zipimport/unknown loader layouts,
extension modules (``.so`` / ``.pyd`` / ``.dylib``) — none are validated here yet.
"""
from __future__ import annotations

import csv
import hashlib
import io
import subprocess
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

# Local build timeout (mirrors installer.PIP_TIMEOUT) — kept local so this
# import-free helper stays independent of the heavy installer module.
WHEEL_BUILD_TIMEOUT = 120

_EXTENSION_SUFFIXES = (".so", ".pyd", ".dylib")


class WheelValidationError(Exception):
    """A wheel could not be built or its distribution identity could not be
    validated import-free. ``code`` is a short, content-free token; ``message`` is
    the safe operator-facing string (no paths, no metadata dumps — §19)."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class WheelIdentity:
    """The locally OBSERVED distribution identity of a validated wheel.

    ``distribution`` is PEP-503 canonical; ``version`` is the canonical
    ``packaging.version.Version`` string. ``top_levels`` are the import top-levels
    the wheel provides (regular packages / modules only). ``entrypoint_top_level``
    is the sealed-entrypoint top-level, confirmed to be among ``top_levels``.
    """
    distribution: str
    version: str
    top_levels: frozenset[str]
    entrypoint_top_level: str


# ---------------------------------------------------------------------------
# Canonicalization (§4) — name and version are SEPARATE identities from the
# ANP manifest version (entry["version"]).
# ---------------------------------------------------------------------------

def canonical_dist_name(name: str) -> str:
    """PEP-503 canonical distribution name: lowercase, ``[-_.]+`` → ``-``.

    Raises :class:`WheelValidationError` for an empty / non-string value.
    """
    if not isinstance(name, str) or not name.strip():
        raise WheelValidationError("metadata_missing", "Distribution metadata missing")
    canon = canonicalize_name(name)
    if not canon:
        raise WheelValidationError("metadata_missing", "Distribution metadata missing")
    return canon


def canonical_dist_version(version: str) -> str:
    """Validate with ``packaging.version.Version`` and return its canonical string.

    Kept strictly separate from the ANP ``entry["version"]`` identity.
    """
    if not isinstance(version, str) or not version.strip():
        raise WheelValidationError("metadata_missing", "Distribution metadata missing")
    try:
        return str(Version(version))
    except InvalidVersion:
        raise WheelValidationError("metadata_ambiguous", "Distribution metadata ambiguous")


def top_level_from_entrypoint(entrypoint: str) -> str:
    """Derive the import top-level from a sealed entrypoint, purely as a string.

    ``pkg.sub.agent:run`` → ``pkg``. Raises for an empty / malformed entrypoint —
    M1 never guesses a top-level from the environment.
    """
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        raise WheelValidationError("entrypoint_namespace", "Entrypoint namespace not provided")
    module_path = entrypoint.split(":", 1)[0]
    top = module_path.split(".", 1)[0].strip()
    if not top:
        raise WheelValidationError("entrypoint_namespace", "Entrypoint namespace not provided")
    return top


# ---------------------------------------------------------------------------
# Build-wheel-first (§5)
# ---------------------------------------------------------------------------

def build_wheel(
    target_python: str, source_dir: Path, dest_dir: Path, *, env: dict | None = None
) -> Path:
    """Build exactly one wheel from a verified source tree into an EMPTY dest dir.

    Runs ``<target_python> -m pip wheel --no-deps --no-cache-dir --wheel-dir
    <dest_dir> <source_dir>``. Requires: exit 0; ``dest_dir`` empty on entry;
    exactly one regular ``.whl`` afterwards (no symlink, fully inside ``dest_dir``,
    never selected by an unvalidated filename). The caller owns ``dest_dir`` and
    cleans it in ``finally``.

    ``env`` (the SINGLE frozen controlled environment) drives the build too — the
    build MUST NOT re-inherit an unmanaged ``os.environ`` with ``PIP_TARGET`` /
    ``PIP_PREFIX`` / user-site overrides. ``None`` inherits (metadata-only callers).

    Build boundary — the build runs BEFORE quarantine. A cooperative PEP-517
    backend does not perform a normal site-packages install (``pip wheel`` targets
    only ``--wheel-dir``). A malicious backend is host-capable code and can cause
    host side effects regardless; M1 does not (and does not claim to) prevent that
    for an already-host-accepted trusted-tier build.
    """
    dest_dir = Path(dest_dir)
    if any(dest_dir.iterdir()):
        raise WheelValidationError("wheel_build_failed", "Wheel build failed")
    cmd = [
        target_python, "-m", "pip", "wheel",
        "--no-deps", "--no-cache-dir",
        "--wheel-dir", str(dest_dir),
        str(source_dir),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=WHEEL_BUILD_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        raise WheelValidationError("wheel_build_failed", "Wheel build failed")
    if proc.returncode != 0:
        raise WheelValidationError("wheel_build_failed", "Wheel build failed")

    entries = list(dest_dir.iterdir())
    wheels = [p for p in entries if p.name.endswith(".whl") and p.is_file() and not p.is_symlink()]
    # Exactly one wheel, and the build produced nothing else (no symlink, no stray
    # file that a filename-based selection could latch onto).
    if len(wheels) != 1 or len(entries) != 1:
        raise WheelValidationError("wheel_build_failed", "Wheel build failed")
    wheel = wheels[0]
    # Defence in depth: the single member must resolve inside dest_dir.
    dest_resolved = dest_dir.resolve()
    if dest_resolved not in wheel.resolve().parents:
        raise WheelValidationError("wheel_build_failed", "Wheel build failed")
    return wheel


# ---------------------------------------------------------------------------
# Import-free wheel validation (§6)
# ---------------------------------------------------------------------------

def _reject_unsafe_member(name: str) -> None:
    # No absolute members, no drive letters, no parent-escape components.
    if name.startswith("/") or name.startswith("\\"):
        raise WheelValidationError("unsupported_layout", "Unsupported package layout")
    if len(name) >= 2 and name[1] == ":":  # windows drive letter
        raise WheelValidationError("unsupported_layout", "Unsupported package layout")
    parts = name.replace("\\", "/").split("/")
    if any(p == ".." for p in parts):
        raise WheelValidationError("unsupported_layout", "Unsupported package layout")


def _read_metadata_name_version(raw: bytes) -> tuple[str, str]:
    msg = BytesParser().parsebytes(raw)
    name = msg.get("Name")
    version = msg.get("Version")
    if not name or not version:
        raise WheelValidationError("metadata_missing", "Distribution metadata missing")
    return name, version


def validate_wheel(wheel_path: Path, expected_top_level: str) -> WheelIdentity:
    """Validate a wheel as a ZIP (no import) and return its observed identity.

    Enforces (§6): exactly one ``*.dist-info/METADATA`` with a matching ``RECORD``;
    ``Name``/``Version`` present, canonical, and coherent with the dist-info dir;
    RECORD syntactically readable; no absolute / ``..`` members; no extension
    modules; and that ``expected_top_level`` is provided as a REGULAR package
    (``__init__.py``) or a top-level module (``.py``) — never a namespace package.
    """
    try:
        zf = zipfile.ZipFile(str(wheel_path))
    except (zipfile.BadZipFile, OSError):
        raise WheelValidationError("metadata_missing", "Distribution metadata missing")
    with zf:
        names = zf.namelist()
        for n in names:
            _reject_unsafe_member(n)

        # Exactly one dist-info METADATA (ambiguous identity is fail-closed).
        metadata_members = [n for n in names if n.endswith(".dist-info/METADATA")]
        if len(metadata_members) == 0:
            raise WheelValidationError("metadata_missing", "Distribution metadata missing")
        if len(metadata_members) > 1:
            raise WheelValidationError("metadata_ambiguous", "Distribution metadata ambiguous")
        metadata_member = metadata_members[0]
        dist_info = metadata_member[: -len("/METADATA")]  # foo-1.0.dist-info

        record_member = dist_info + "/RECORD"
        if record_member not in names:
            raise WheelValidationError("metadata_missing", "Distribution metadata missing")

        name, version = _read_metadata_name_version(zf.read(metadata_member))
        canon_name = canonical_dist_name(name)
        canon_version = canonical_dist_version(version)

        # Coherence: the dist-info dir encodes {escaped_name}-{version}. Compare the
        # canonicalized dir-name and version to the METADATA values.
        stem = dist_info[: -len(".dist-info")]
        if "-" not in stem:
            raise WheelValidationError("metadata_ambiguous", "Distribution metadata ambiguous")
        dir_name, dir_version = stem.rsplit("-", 1)
        if canonicalize_name(dir_name) != canon_name:
            raise WheelValidationError("metadata_ambiguous", "Distribution metadata ambiguous")
        try:
            if Version(dir_version) != Version(canon_version):
                raise WheelValidationError("metadata_ambiguous", "Distribution metadata ambiguous")
        except InvalidVersion:
            raise WheelValidationError("metadata_ambiguous", "Distribution metadata ambiguous")

        # RECORD must be syntactically readable (CSV). Content is not trusted.
        try:
            record_text = zf.read(record_member).decode("utf-8")
            list(csv.reader(io.StringIO(record_text)))
        except (UnicodeDecodeError, csv.Error, OSError):
            raise WheelValidationError("metadata_ambiguous", "Distribution metadata ambiguous")

        # No extension modules in the payload (fail-closed until explicitly tested).
        for n in names:
            low = n.lower()
            if any(low.endswith(suf) for suf in _EXTENSION_SUFFIXES):
                raise WheelValidationError("unsupported_layout", "Unsupported package layout")

        # Derive the provided top-levels from the actual payload (namelist is the
        # authority; top_level.txt, if present, does not prove regular-vs-namespace).
        top_levels = _derive_top_levels(names, dist_info)

        # The sealed-entrypoint top-level must be provided as a regular package or
        # module — a bare namespace directory is refused.
        _require_regular_top_level(names, expected_top_level)

        return WheelIdentity(
            distribution=canon_name,
            version=canon_version,
            top_levels=frozenset(top_levels),
            entrypoint_top_level=expected_top_level,
        )


def _payload_members(names: list[str], dist_info: str) -> list[str]:
    """Members that are import payload — excludes the dist-info and .data dirs."""
    data_dir_prefixes = tuple(
        n[: n.index("/") + 1]
        for n in names
        if ".data/" in n and n.split("/", 1)[0].endswith(".data")
    )
    out = []
    for n in names:
        if n.startswith(dist_info + "/"):
            continue
        if data_dir_prefixes and n.startswith(data_dir_prefixes):
            continue
        if not n or n.endswith("/"):
            continue
        out.append(n)
    return out


def _derive_top_levels(names: list[str], dist_info: str) -> set[str]:
    """Deterministically derive REGULAR import top-levels from the payload.

    A top-level is included only if it is a regular package (``<top>/__init__.py``)
    or a top-level module (``<top>.py``). Bare namespace directories are excluded.
    """
    payload = _payload_members(names, dist_info)
    packages: set[str] = set()
    namespace_dirs: set[str] = set()
    modules: set[str] = set()
    for n in payload:
        norm = n.replace("\\", "/")
        if "/" in norm:
            top = norm.split("/", 1)[0]
            if norm == f"{top}/__init__.py":
                packages.add(top)
            else:
                namespace_dirs.add(top)
        elif norm.endswith(".py"):
            modules.add(norm[:-3])
    # A dir that never showed an __init__.py is a namespace package → not a regular
    # top-level. Regular packages win if BOTH signals appear for the same name.
    return packages | (modules - namespace_dirs)


def _require_regular_top_level(names: list[str], top: str) -> None:
    has_init = f"{top}/__init__.py" in names or f"{top}\\__init__.py" in names
    has_module = f"{top}.py" in names
    has_dir = any(n.replace("\\", "/").startswith(f"{top}/") for n in names)
    if has_init or has_module:
        return
    if has_dir:
        # directory present but no __init__.py → PEP 420 namespace package
        raise WheelValidationError("entrypoint_namespace", "Entrypoint namespace not provided")
    raise WheelValidationError("entrypoint_namespace", "Entrypoint namespace not provided")


# ---------------------------------------------------------------------------
# Internal wheel consistency hash (§7) — NOT stored, NOT an A3 attestation.
# ---------------------------------------------------------------------------

def wheel_consistency_hash(wheel_path: Path) -> str:
    """SHA-256 of the built wheel bytes.

    Used only to confirm the locally validated wheel is unchanged between
    validation and pip hand-off. It is never written to the lockfile and makes no
    cryptographic claim against a malicious same-user process (§7).
    """
    h = hashlib.sha256()
    with open(wheel_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
