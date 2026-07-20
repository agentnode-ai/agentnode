"""Local package installation for AgentNode SDK.

Ports the CLI install flow (§13.4) to Python so agents can
install capabilities programmatically without human intervention.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from agentnode_sdk.exceptions import LockfileFormatError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCKFILE_NAME = "agentnode.lock"
LOCKFILE_VERSION = "0.1"
MAX_FILES_IN_ARCHIVE = 500
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB (per file)
# Hard ceiling for artifact downloads. Prevents a malicious or misbehaving
# server from filling the disk with an unbounded stream.
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
DOWNLOAD_TIMEOUT = 120.0
PIP_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Python interpreter resolution (Spec §13.3)
# ---------------------------------------------------------------------------

def resolve_python() -> str:
    """Find a usable Python 3 interpreter for the host package build.

    Resolution order:
    0. ``sys.executable`` — the interpreter actually running AgentNode, so a host
       build (``agentnode install``) installs into the SAME environment that
       ``agentnode run`` later imports from. This fixes pipx / unactivated-venv
       installs, where ``$VIRTUAL_ENV`` is unset and ``python`` on PATH is a
       DIFFERENT interpreter (the package would install into the wrong env and
       ``agentnode run`` would fail to import it).
    1. $VIRTUAL_ENV/bin/python (or Scripts/python.exe on Windows)
    2. .venv/bin/python in cwd
    3. python3 on PATH
    4. python on PATH
    """
    is_windows = sys.platform == "win32"

    # 0. The interpreter actually running AgentNode — guarantees install and run
    #    use the same Python. Most reliable; prefer it over PATH/venv heuristics.
    if sys.executable and os.path.isfile(sys.executable):
        return sys.executable

    # 1. Active virtual environment
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        bin_path = (
            os.path.join(venv, "Scripts", "python.exe")
            if is_windows
            else os.path.join(venv, "bin", "python")
        )
        if os.path.isfile(bin_path) and _is_python3(bin_path):
            return bin_path

    # 2. Local .venv
    local_venv = (
        os.path.join(os.getcwd(), ".venv", "Scripts", "python.exe")
        if is_windows
        else os.path.join(os.getcwd(), ".venv", "bin", "python")
    )
    if os.path.isfile(local_venv) and _is_python3(local_venv):
        return local_venv

    # 3. python3 on PATH
    py3 = _try_python("python3")
    if py3:
        return py3

    # 4. python on PATH
    py = _try_python("python")
    if py:
        return py

    raise RuntimeError(
        "No Python 3 interpreter found. "
        "Activate a virtual environment or ensure python3 is on PATH."
    )


def _is_python3(path: str) -> bool:
    try:
        out = subprocess.check_output(
            [path, "--version"], stderr=subprocess.STDOUT, timeout=5
        ).decode().strip()
        return out.startswith("Python 3.")
    except Exception:
        return False


def _try_python(cmd: str) -> str | None:
    try:
        out = subprocess.check_output(
            [cmd, "--version"], stderr=subprocess.STDOUT, timeout=5
        ).decode().strip()
        if out.startswith("Python 3."):
            return cmd
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_artifact(
    url: str,
    dest: Path,
    *,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> None:
    """Download artifact from presigned URL to *dest*.

    Enforces a ``max_bytes`` ceiling so a malicious or misbehaving server
    cannot stream unbounded data into the local filesystem. If the server
    declares a ``Content-Length`` header that exceeds the limit, the
    download is refused before any bytes are written. Otherwise, the
    stream is checked per chunk.
    """
    with httpx.stream("GET", url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as resp:
        resp.raise_for_status()

        declared = resp.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > max_bytes:
                    raise RuntimeError(
                        f"Artifact too large: {declared} bytes "
                        f"(max {max_bytes}). Refusing download."
                    )
            except ValueError:
                pass  # Ignore malformed Content-Length

        written = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=8192):
                written += len(chunk)
                if written > max_bytes:
                    f.close()
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                    raise RuntimeError(
                        f"Artifact exceeded max size ({max_bytes} bytes) "
                        "during download. Aborted."
                    )
                f.write(chunk)


# ---------------------------------------------------------------------------
# Hash verification
# ---------------------------------------------------------------------------

def verify_hash(file_path: Path, expected: str | None) -> str:
    """Compute SHA256 of *file_path*. Raise on mismatch if *expected* given.

    Returns the hex digest.
    """
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    digest = sha.hexdigest()

    if expected:
        clean = expected.removeprefix("sha256:")
        if digest != clean:
            raise RuntimeError(
                f"Hash mismatch! Expected {clean}, got {digest}. "
                "The artifact may have been tampered with."
            )
    return digest


# ---------------------------------------------------------------------------
# Archive extraction & validation
# ---------------------------------------------------------------------------

def extract_archive(tar_path: Path, dest: Path) -> Path:
    """Extract tar.gz, validate security, return package root directory."""
    with tarfile.open(tar_path, "r:gz") as tf:
        members = tf.getmembers()

        # Security checks
        if len(members) > MAX_FILES_IN_ARCHIVE:
            raise RuntimeError(
                f"Archive contains {len(members)} files (max {MAX_FILES_IN_ARCHIVE})."
            )

        for m in members:
            # Reject path traversal
            if ".." in m.name or m.name.startswith("/"):
                raise RuntimeError(f"Unsafe path in archive: {m.name}")
            # Reject symlinks
            if m.issym() or m.islnk():
                raise RuntimeError(f"Symlinks not allowed in archive: {m.name}")
            # Reject oversized files
            if m.size > MAX_FILE_SIZE_BYTES:
                raise RuntimeError(
                    f"File too large: {m.name} ({m.size} bytes, max {MAX_FILE_SIZE_BYTES})."
                )

        tf.extractall(dest, filter="data")

    # Handle single-root-directory archives (unwrap if needed)
    entries = list(dest.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest


def _verify_entrypoint(package_dir: Path, entrypoint: str | None) -> None:
    """Non-fatal check that entrypoint module file exists."""
    if not entrypoint:
        return
    # Strip :function suffix for v0.2 entrypoints (module.path:function → module.path)
    module_path = entrypoint.split(":")[0] if ":" in entrypoint else entrypoint
    # Convert module.path → module/path.py
    rel = module_path.replace(".", os.sep) + ".py"
    candidates = [
        package_dir / rel,
        package_dir / "src" / rel,
    ]
    if not any(c.is_file() for c in candidates):
        # Non-fatal, just warn — the module may install differently
        pass


# ---------------------------------------------------------------------------
# pip install
# ---------------------------------------------------------------------------

def pip_install(python: str, package_dir: Path, verbose: bool = False) -> None:
    """Install package from extracted directory using pip."""
    cmd = [python, "-m", "pip", "install", str(package_dir)]
    if not verbose:
        cmd.append("--quiet")
    try:
        subprocess.check_call(cmd, timeout=PIP_TIMEOUT)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"pip install failed (exit code {exc.returncode})") from exc
    except subprocess.TimeoutExpired:
        raise RuntimeError("pip install timed out after 120 seconds")


def _make_container_readable(root: Path) -> None:
    """Widen perms on a TEMPORARY extracted+verified artifact dir so the sandbox
    container's unprivileged user (uid 1000) can read it when mounted at /src:ro.

    ``tempfile.mkdtemp()`` creates mode 0700, so uid 1000 inside the container
    can't read the mount (mirrors the proven recipe in
    ``backend/app/verification/sandbox.py``). Dirs → 0o755 (traverse), files →
    0o644 (read).

    SCOPE — this is applied ONLY to the ephemeral, hash-verified artifact
    directory under our own tempdir. It must NEVER be applied to a user
    workspace, to ``~/.agentnode``, or to any user-chosen local path. Do not
    reuse this for a future local-dev / workspace mount.
    """
    try:
        os.chmod(root, 0o755)
    except OSError:
        pass
    for dpath, dirs, files in os.walk(root):
        for d in dirs:
            try:
                os.chmod(os.path.join(dpath, d), 0o755)
            except OSError:
                pass
        for f in files:
            try:
                os.chmod(os.path.join(dpath, f), 0o644)
            except OSError:
                pass


def _container_build_into_volume(
    slug: str,
    version: str,
    package_dir: Path,
    artifact_hash: str,
) -> str:
    """P0.3: build a non-trusted toolpack INSIDE the container into a deterministic
    volume. setup.py / PEP-517 hooks run isolated, NEVER on the host.

    Returns the volume name (recorded in the lockfile and re-checked at run time).
    Fail-closed: raises ``SandboxRequiredError`` if no container runtime is
    available — there is no host build fallback for community code.
    """
    from agentnode_sdk.sandbox import (
        SandboxRequiredError,
        get_default_backend,
        sandbox_volume_name,
    )
    from agentnode_sdk.sandbox.types import MountSpec

    backend = get_default_backend()
    availability = backend.check_available()
    if not availability.available:
        raise SandboxRequiredError(
            f"Refusing to build non-trusted package '{slug}@{version}' on the host: "
            "community toolpack builds require a container runtime (Docker or Podman). "
            f"{availability.reason or 'None detected'} — no host build fallback."
        )

    runtime = availability.backend or "docker"
    volume = sandbox_volume_name(slug, version, artifact_hash)

    def _rm_volume() -> None:
        try:
            subprocess.run([runtime, "volume", "rm", volume], capture_output=True, timeout=30)
        except Exception:
            pass

    # Start from a clean volume so a re-install never layers onto a stale build.
    _rm_volume()

    # The extracted artifact dir is 0700 (mkdtemp) → uid 1000 in the container
    # can't read the /src:ro mount. Widen perms on this ephemeral verified dir.
    _make_container_readable(package_dir)

    # The BUILD container mounts the verified source (ro) + the volume (rw). pip
    # must fetch dependencies, so the build keeps network=default; the RUN
    # container's network is separately derived from the declared permission.
    #
    # setuptools writes its `build/` dir into the source tree (cwd), but /src is
    # mounted read-only (and the rootfs is read-only). So copy the verified source
    # into a writable /tmp dir first and build from there — /src stays read-only
    # (the build can't mutate the verified artifact). `python -m pip` (not bare
    # `pip`) resolves via the image's python; a larger /tmp tmpfs + TMPDIR give the
    # copy + build room under the read-only rootfs (only /tmp and the volume are
    # writable).
    spec = backend.build_process_spec(
        ["sh", "-c",
         "cp -r /src /tmp/build-src && "
         "python -m pip install --no-input --no-cache-dir --target /install /tmp/build-src"],
        network="default",
        mounts=[
            MountSpec(src=str(package_dir), dst="/src", read_only=True),
            MountSpec(src=volume, dst="/install", read_only=False),
        ],
        env={"PIP_NO_CACHE_DIR": "1", "TMPDIR": "/tmp"},
        limits={"tmp_size": "512m"},
        clean_home=True,
    )
    returncode, stdout, stderr = backend.run_process(spec, timeout=PIP_TIMEOUT)
    if returncode != 0:
        _rm_volume()  # don't leave a half-built volume behind
        raise RuntimeError(
            f"Sandboxed build failed for '{slug}@{version}' (exit {returncode}): "
            f"{(stderr or stdout).strip()[:2000]}"
        )
    return volume


# ---------------------------------------------------------------------------
# MCP pre-install (Stage 4A) — descriptor validation + build into sealed volume
# ---------------------------------------------------------------------------

# npm package: optional @scope/, then a name; lowercase per npm rules.
_NPM_PACKAGE = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")
# strict semver x.y.z with optional -prerelease / +build (no ranges/operators/tags).
_NPM_SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z][0-9A-Za-z.-]*)?(\+[0-9A-Za-z][0-9A-Za-z.-]*)?$")
# PEP 503 normalized name.
_PYPI_PACKAGE = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
# exact PEP 440 release (no operators): N(.N)*, optional pre/post/dev — NOT a specifier.
_PYPI_VERSION = re.compile(
    r"^[0-9]+(\.[0-9]+)*((a|b|rc)[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?$"
)

# Stage 4A/4B: the deterministic tree-hasher lives in the SHARED module so the build
# hash (here) and the run-time verify hash (Stage 4B) can never drift. See
# agentnode_sdk.sandbox.mcp_preinstall._MCP_HASH_PY for the full description.
from agentnode_sdk.sandbox.mcp_preinstall import _MCP_HASH_PY  # noqa: E402


def _reject_unpinned_version(version: str) -> None:
    """Raise ValueError for anything that is not an exact, pinned release —
    floating/latest/wildcards/ranges/operators/dist-tags/url/vcs/workspace specs."""
    v = (version or "").strip()
    low = v.lower()
    if not v or low in ("latest", "*", "x", "current", "stable"):
        raise ValueError(f"floating version not allowed: {version!r}")
    if any(ch in v for ch in "^~<>= |@:/\\ \t"):
        raise ValueError(f"version operator/range/spec not allowed: {version!r}")
    if "||" in v or " - " in v:
        raise ValueError(f"version range not allowed: {version!r}")
    if re.search(r"(^|\.)[xX*]($|\.)", v):
        raise ValueError(f"wildcard version not allowed: {version!r}")
    for tok in ("git+", "http://", "https://", "file:", "workspace:", "link:",
                "github:", "git:", "git@"):
        if tok in low:
            raise ValueError(f"url/vcs/workspace spec not allowed: {version!r}")


def validate_mcp_install(descriptor: Any) -> tuple[str, str, str]:
    """Validate an ``mcp_install`` descriptor (pure; no I/O, no command parsing).

    Returns the canonical ``(manager, package, version)`` or raises ``ValueError``.
    ``mcp_install`` is the ONLY source — ``mcp_command`` is never parsed. Only
    ``npm``/``pypi`` with an exact pinned version are accepted; floating/latest/
    ranges/operators/git/url/branch/tag/file/workspace specs are refused.
    """
    if not isinstance(descriptor, dict):
        raise ValueError("mcp_install must be an object")
    extra = set(descriptor) - {"manager", "package", "version"}
    if extra:
        raise ValueError(f"mcp_install has unexpected keys: {sorted(extra)}")
    manager = descriptor.get("manager")
    package = descriptor.get("package")
    version = descriptor.get("version")
    if not all(isinstance(x, str) for x in (manager, package, version)):
        raise ValueError("mcp_install.manager/package/version must all be strings")
    manager = manager.strip().lower()
    package = package.strip()
    version = version.strip()
    if manager not in ("npm", "pypi"):
        raise ValueError(f"mcp_install.manager must be 'npm' or 'pypi', got {manager!r}")
    if not package:
        raise ValueError("mcp_install.package must be non-empty")
    _reject_unpinned_version(version)
    if manager == "npm":
        if not _NPM_PACKAGE.match(package):
            raise ValueError(f"invalid npm package name: {package!r}")
        if not _NPM_SEMVER.match(version):
            raise ValueError(f"npm version must be exact semver x.y.z, got {version!r}")
    else:  # pypi
        norm = re.sub(r"[-_.]+", "-", package.lower())
        if "[" in package or "]" in package or not _PYPI_PACKAGE.match(norm):
            raise ValueError(f"invalid pypi package name: {package!r}")
        if not _PYPI_VERSION.match(version):
            raise ValueError(
                f"pypi version must be an exact PEP 440 release (no operators), got {version!r}"
            )
        package = norm
    return manager, package, version


def _container_build_mcp_volume(
    slug: str, version: str, manager: str, package: str, pkg_version: str,
) -> tuple[str, str, list[str]]:
    """Stage 4A: build a pinned MCP package INSIDE the container into a deterministic
    sealed volume. Returns ``(volume, tree_hash, preinstall_command)``.

    Build security: pinned base image; ``network="default"`` ONLY for this build (the
    later run path is unaffected); clean HOME, empty env — NO host HOME, NO .npmrc/
    .pypirc, NO SSH/git/token files, NO host credentials; ONLY the rw target volume is
    mounted (install is by name from the registry, no /src). Fail-closed: raises
    ``SandboxRequiredError`` if no runtime/image — no host build fallback.
    """
    from agentnode_sdk.sandbox import SandboxRequiredError, get_default_backend
    from agentnode_sdk.sandbox.container_backend import mcp_sandbox_volume_name
    from agentnode_sdk.sandbox.types import MountSpec

    backend = get_default_backend()
    availability = backend.check_available()
    if not availability.available:
        raise SandboxRequiredError(
            f"Refusing to pre-install MCP '{slug}@{version}': a container runtime "
            f"(Docker or Podman) + the pinned image are required. "
            f"{availability.reason or 'None detected'} — no host build fallback."
        )

    runtime = availability.backend or "docker"
    volume = mcp_sandbox_volume_name(slug, version, manager, package, pkg_version)

    def _rm_volume() -> None:
        try:
            subprocess.run([runtime, "volume", "rm", volume], capture_output=True, timeout=30)
        except Exception:
            pass

    _rm_volume()  # clean volume so a re-install never layers onto a stale build

    if manager == "npm":
        install_cmd = f"npm install -g --prefix /install '{package}@{pkg_version}'"
    else:
        install_cmd = f"uv pip install --target /install '{package}=={pkg_version}'"

    # Install by name (network=default; install noise → stderr), then run our
    # deterministic Python tree-hasher (no host tool, no secrets) which prints the two
    # marker lines `HASH:` and `BINS:` on stdout. `python3 -c <code>` is shell-quoted via
    # shlex so the embedded code is passed verbatim regardless of its content.
    script = (
        "set -e; "
        f"{install_cmd} 1>&2; "
        f"python3 -c {shlex.quote(_MCP_HASH_PY)}"
    )
    spec = backend.build_process_spec(
        ["sh", "-c", script],
        network="default",
        mounts=[MountSpec(src=volume, dst="/install", read_only=False)],
        env={},
        limits={"tmp_size": "512m"},
        clean_home=True,
    )
    returncode, stdout, stderr = backend.run_process(spec, timeout=PIP_TIMEOUT)
    if returncode != 0:
        _rm_volume()
        raise RuntimeError(
            f"MCP pre-install build failed for '{slug}@{version}' (exit {returncode}): "
            f"{(stderr or stdout).strip()[:2000]}"
        )

    tree_hash = ""
    bins: list[str] = []
    for line in (stdout or "").splitlines():
        if line.startswith("HASH:"):
            tree_hash = line[len("HASH:"):].strip()
        elif line.startswith("BINS:"):
            bins = [b for b in line[len("BINS:"):].strip().split(",") if b]
    if not tree_hash or not bins:
        _rm_volume()
        raise RuntimeError(
            f"MCP pre-install produced no entrypoint for '{slug}@{version}' "
            f"(bins={bins or 'none'}) — refusing to seal an unusable volume."
        )

    # Resolve the entrypoint bin: prefer one matching the package's short name, else
    # the single bin, else the first (sorted). Stored for Stage 4B — NOT consumed in 4A.
    short = package.split("/")[-1]
    chosen = next((b for b in bins if b == short), bins[0] if len(bins) == 1 else
                  next((b for b in bins if short in b), sorted(bins)[0]))
    interp = "node" if manager == "npm" else "python"
    preinstall_command = [interp, f"/install/bin/{chosen}"]
    return volume, f"sha256:{tree_hash}", preinstall_command


# ---------------------------------------------------------------------------
# Lockfile management
# ---------------------------------------------------------------------------

def _lockfile_path() -> Path:
    override = os.environ.get("AGENTNODE_LOCKFILE")
    if override:
        return Path(override)
    return Path.cwd() / LOCKFILE_NAME


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    """``json`` object_pairs_hook — reject duplicate keys at ANY nesting level.

    JSON permits duplicate keys; ``json.loads`` keeps last-wins, which would let a
    tampered lockfile silently hide an entry or field (the structure digest in a
    later slice would then never see it). Fail closed on the FIRST duplicate.
    Only the key name is surfaced — never a value or file content. Arrays and
    unique-keyed objects are unaffected (this hook fires only for JSON objects).
    """
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise LockfileFormatError(
                "lockfile_format",
                f"duplicate key {key!r} in agentnode.lock (malformed or tampered)",
            )
        seen.add(key)
    return dict(pairs)


def read_lockfile(path: Path | None = None) -> dict:
    lf = path or _lockfile_path()
    if lf.is_file():
        try:
            # object_pairs_hook rejects duplicate keys (raises LockfileFormatError,
            # which is NOT caught below — a duplicate-key lockfile fails closed
            # rather than being swallowed into an empty default).
            data = json.loads(
                lf.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
            )
            if isinstance(data, dict) and "packages" in data:
                return data
        except (json.JSONDecodeError, OSError):
            import logging
            logging.getLogger("agentnode.installer").warning(
                "Lockfile corrupted or unreadable: %s — treating as empty", lf
            )
    return {"lockfile_version": LOCKFILE_VERSION, "updated_at": "", "packages": {}}


# Lockfile schema versions this SDK can safely mutate. A file whose
# lockfile_version is absent or outside this set is refused for mutation (never
# silently "repaired"): 0.2A-2a has no historical migration path for it.
_SUPPORTED_LOCKFILE_VERSIONS: tuple[str, ...] = (LOCKFILE_VERSION,)


def _new_lockfile() -> dict:
    return {"lockfile_version": LOCKFILE_VERSION, "updated_at": "", "packages": {}}


def read_lockfile_strict(path: Path | None = None) -> dict | None:
    """Read agentnode.lock for MUTATION — fail-closed, never fail-soft.

    Unlike :func:`read_lockfile` (fail-soft for read-only callers), an EXISTING
    file that is unreadable / syntactically invalid / duplicate-keyed / has an
    invalid base model raises :class:`LockfileFormatError` so the mutation is
    refused — a broken file is NEVER treated as empty and overwritten. Returns
    ``None`` when the file does not exist (a fresh lockfile may be created).
    """
    lf = path or _lockfile_path()
    if not lf.is_file():
        return None
    try:
        text = lf.read_text(encoding="utf-8")
    except OSError:
        raise LockfileFormatError("lockfile_unreadable", "agentnode.lock could not be read")
    try:
        # object_pairs_hook raises LockfileFormatError on duplicate keys (propagates).
        data = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError:
        raise LockfileFormatError("lockfile_invalid_json", "agentnode.lock is not valid JSON")
    # Base model for a mutation: a top-level dict, a dict ``packages``, and a
    # supported ``lockfile_version`` string. A missing/unsupported version is NOT
    # auto-repaired — the mutation fails closed rather than silently normalizing a
    # base model the last review rejected healing.
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("packages"), dict)
        or data.get("lockfile_version") not in _SUPPORTED_LOCKFILE_VERSIONS
    ):
        raise LockfileFormatError("lockfile_invalid_model", "agentnode.lock has an invalid base model")
    return data


def _require_mutable_structure(data: dict) -> str:
    """Pre-mutation integrity gate — checks BOTH levels of the integrity chain.

    A normal mutation must never conserve or heal existing drift, so an existing
    lockfile is refused unless every existing per-entry integrity AND the global
    structure digest are intact. The structure digest is a hash-of-hashes over the
    per-entry ``_integrity`` values, so a manipulated entry whose ``_integrity``
    was left untouched keeps the structure ``"verified"`` while the entry itself is
    ``"mismatch"`` — the per-entry level is therefore checked independently, with
    NO exception for the slug being changed or removed.

    Returns the structure status (``"verified"`` or ``"missing"``) when the
    mutation may proceed; otherwise raises :class:`LockfileFormatError` (no write).
    Messages are neutral (no lockfile values) and point to ``agentnode lock seal``
    (unsealed) or ``agentnode lock verify`` / ``lock seal --force`` (drift).
    """
    from agentnode_sdk.lock_integrity import verify_entry, verify_structure

    # (1) Every existing per-entry integrity — checked FIRST so an unsealed entry
    # routes to plain `lock seal` (not the deliberate --force reseal), and so
    # content drift is refused even when the global structure digest still matches.
    for slug, entry in data["packages"].items():
        if not isinstance(entry, dict):
            raise LockfileFormatError(
                "lockfile_entry_invalid",
                "an existing lockfile entry is not an object — run `agentnode lock verify`",
            )
        est = verify_entry(slug, entry).status
        if est == "verified":
            continue
        if est == "missing":
            raise LockfileFormatError(
                "lockfile_unsealed_entries",
                "existing lockfile entries are not fully sealed — run `agentnode lock seal` first",
            )
        # "mismatch" (or any other non-verified status): deliberate reseal only.
        raise LockfileFormatError(
            "lockfile_entry_mismatch",
            "an existing lockfile entry fails its integrity check — run `agentnode lock verify`; "
            "if the change is intended, reseal deliberately with `agentnode lock seal --force`",
        )

    # (2) Global structure digest over the (now individually-verified) set.
    status = verify_structure(data)
    if status in ("verified", "missing"):
        return status
    raise LockfileFormatError(
        f"lockfile_structure_{status}",
        "lockfile structure check failed — run `agentnode lock verify`; if the change "
        "is intended, reseal deliberately with `agentnode lock seal --force`",
    )


def _audit_structure_event(reason: str, slug: str | None) -> None:
    """Best-effort audit for a structure-digest migration (never crashes)."""
    try:
        from agentnode_sdk.policy import PolicyResult, audit_decision
        audit_decision(
            PolicyResult(action="allow", reason=reason, source="lock_integrity"),
            "lockfile_structure",
            slug or "-",
        )
    except Exception:
        pass


def mutate_lockfile(
    apply, *, path: Path | None = None, audit_slug: str | None = None,
    durable: bool = False,
) -> None:
    """Central, structure-safe lockfile mutation — the single writer path.

    Order (all under one ``file_lock``): mutation-strict read -> pre-mutation
    structure gate -> ``apply(data)`` in memory -> ``seal_structure`` (last, over
    the post-mutation set) -> single ``updated_at`` -> atomic write. ``apply``
    returns a truthy value iff it changed the set; a falsey return is a
    transactional no-op — NOTHING is written, resealed, restamped, or audited.
    Refuses (raises, no write) a corrupt / mismatch / invalid existing lockfile.
    No partial state ever reaches disk.

    ``durable=True`` (A1-M1 lock transaction only) fsyncs the write. The
    per-lockfile ``file_lock`` here is the INNER lock — never held across a pip
    call; the A1-M1 environment lock is the OUTER lock (see ``_env_lock``).
    """
    from agentnode_sdk._fileutil import atomic_write_json, file_lock
    from agentnode_sdk.lock_integrity import seal_structure

    lf = path or _lockfile_path()
    did_write = False
    migrating = False
    with file_lock(lf):
        data = read_lockfile_strict(lf)
        if data is None:
            data = _new_lockfile()
        else:
            migrating = _require_mutable_structure(data) == "missing"
        if not apply(data):                            # transactional no-op -> no write
            return
        sealed = seal_structure(data)                  # last; raises if any entry invalid
        sealed["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(lf, sealed, durable=durable)
        did_write = True
    if did_write and migrating:
        _audit_structure_event("structure_digest_migration", audit_slug)


def update_lockfile(
    slug: str,
    entry: dict[str, Any],
    path: Path | None = None,
    *,
    durable: bool = False,
) -> None:
    """Write or update a package entry in agentnode.lock (structure-safe)."""
    from agentnode_sdk.lock_integrity import seal_entry

    def _apply(data: dict) -> bool:
        data["packages"][slug] = seal_entry(entry)   # seal the changed entry (idempotent)
        return True                                   # an upsert always changes the set

    mutate_lockfile(_apply, path=path, audit_slug=slug, durable=durable)


def remove_from_lockfile(
    slug: str, path: Path | None = None, *, durable: bool = False
) -> dict | None:
    """Remove *slug* from agentnode.lock (structure-safe). Returns the removed
    entry (for post-removal cleanup) or None. The digest is rebuilt over the
    post-removal set."""
    removed: dict = {}

    def _apply(data: dict) -> bool:
        if slug not in data["packages"]:
            return False                              # transactional no-op: slug absent
        removed["entry"] = data["packages"].pop(slug)
        return True

    mutate_lockfile(_apply, path=path, audit_slug=slug, durable=durable)
    return removed.get("entry")


def check_installed(slug: str, version: str, path: Path | None = None) -> str:
    """Check lockfile status. Returns 'same', 'different', or 'missing'."""
    data = read_lockfile(path)
    pkg = data.get("packages", {}).get(slug)
    if not pkg:
        return "missing"
    return "same" if pkg.get("version") == version else "different"


# ---------------------------------------------------------------------------
# Agent-Exec A1-M1: host-agent install transaction (locally sealed distribution
# metadata + current-entry transaction). Build-wheel-first → import-free wheel
# validation → environment lock → CAS + current-lockfile cross-slug ownership +
# name-change fail-closed → durable pre-pip quarantine → controlled two-step pip →
# target-interpreter post-verify → durable sealed commit.
#
# HONEST BOUNDARY (comment + PR report): the environment lock serializes INSTALLERS,
# not agent EXECUTIONS. The quarantine touches only the CURRENT entry in the CURRENT
# lockfile — another lockfile may keep dispatching an agent into the same environment
# during a pip mutation, and an already-gated call remains possible. Environment-wide
# runtime quarantine and cross-lockfile ownership are A1-Enforcement scope, NOT M1.
# ---------------------------------------------------------------------------

_AGENT_UNAVAILABLE_MSG = (
    "Installation state may have changed. The agent is unavailable until it is "
    "reinstalled."
)

_CONCURRENT_MSG = (
    "Installation could not be completed: the agent entry was changed by a "
    "concurrent lockfile update."
)


class _CASConflict(Exception):
    """The slug diverged from the pre-build baseline (a newer install superseded us)."""


class _OwnershipConflict(Exception):
    """Another current-lockfile agent owns the same distribution or import top-level."""


class _NameChange(Exception):
    """The agent's pip distribution name changed on upgrade (no implicit migration)."""


class _AgentTransactionAbort(Exception):
    """A pre-env-mutation abort carrying a specific, safe operator message."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class _ForeignEntryPresent(Exception):
    """The slug was (re)inserted by another writer between quarantine and commit —
    the commit must NOT overwrite it."""


class _IndeterminateLockState(RuntimeError):
    """The final lock state could not be confirmed (neither the intended entry nor a
    confirmed-absent slug). Never claimed as 'absent', never a success."""


def _capture_cas_baseline(slug: str, path: Path) -> tuple[bool, str | None]:
    """Read the slug's baseline state (mutation-strict) BEFORE the build.

    Returns ``(absent, integrity_hash)``. Raises :class:`LockfileFormatError` on a
    corrupt lockfile (fail-closed pre-build, old entry untouched).
    """
    data = read_lockfile_strict(path)
    if data is None or slug not in data.get("packages", {}):
        return True, None
    entry = data["packages"][slug]
    integ = entry.get("_integrity") if isinstance(entry, dict) else None
    return False, (integ.get("hash") if isinstance(integ, dict) else None)


def _stored_matches_expected(data: Any, slug: str, expected: dict, expected_hash: str) -> bool:
    """Pure predicate: the lockfile *data* holds EXACTLY the expected sealed entry
    for *slug* (valid structure + entry integrity, matching integrity hash, the
    python_distribution pair, and full sealed-field equality)."""
    from agentnode_sdk.lock_integrity import verify_entry, verify_structure

    stored = data["packages"].get(slug) if isinstance(data, dict) and isinstance(
        data.get("packages"), dict
    ) else None
    return bool(
        isinstance(stored, dict)
        and verify_structure(data) == "verified"
        and verify_entry(slug, stored).status == "verified"
        and stored.get("_integrity", {}).get("hash") == expected_hash
        and stored.get("python_distribution") == expected.get("python_distribution")
        and stored.get("python_distribution_version")
        == expected.get("python_distribution_version")
        and stored == expected
    )


def _entry_is_exact(current: Any, expected: dict, expected_hash: str) -> bool:
    """True iff *current* is EXACTLY our expected sealed entry — the integrity hash
    AND full sealed-dict equality (never a single-field match)."""
    return bool(
        isinstance(current, dict)
        and current.get("_integrity", {}).get("hash") == expected_hash
        and current == expected
    )


def _recover_to_absent(slug: str, path: Path, expected: dict, expected_hash: str) -> None:
    """Atomic compare-and-remove of OUR OWN expected entry — never a foreign entry.

    The ownership decision AND the removal happen inside ONE ``mutate_lockfile``
    callback (single lockfile lock), so no writer can slip a foreign entry in
    between a check and a remove. Returns only when the slug is confirmed absent
    (was already absent, or we removed our exact entry). Raises
    :class:`_ForeignEntryPresent` when the slug holds another writer's entry (left
    untouched). Raises :class:`_IndeterminateLockState` when the state cannot be
    mutation-strict determined, or when the slug reappears after our removal.
    """
    decision: dict[str, str] = {}

    def _apply(data: dict) -> bool:
        current = data["packages"].get(slug)
        if current is None:
            decision["state"] = "absent"
            return False                              # no-op — nothing to remove
        if _entry_is_exact(current, expected, expected_hash):
            data["packages"].pop(slug)               # remove ONLY our exact entry
            decision["state"] = "removed"
            return True
        decision["state"] = "foreign"                # someone else's entry — leave it
        return False

    try:
        mutate_lockfile(_apply, path=path, audit_slug=slug, durable=True)
    except Exception as exc:
        raise _IndeterminateLockState(
            "A1-M1 indeterminate lock state: recovery could not read or mutate the lockfile."
        ) from exc

    state = decision.get("state")
    if state == "foreign":
        raise _ForeignEntryPresent()
    if state not in ("absent", "removed"):
        raise _IndeterminateLockState(
            "A1-M1 indeterminate lock state: recovery outcome could not be determined."
        )
    # Subsequent absence confirmation (the ownership DECISION above was atomic; this
    # only verifies). A reappeared slug is a foreign re-insert — leave it, don't remove.
    try:
        confirm = read_lockfile_strict(path)
    except Exception as exc:
        raise _IndeterminateLockState(
            "A1-M1 indeterminate lock state: absence could not be confirmed."
        ) from exc
    if confirm is not None and slug in confirm.get("packages", {}):
        raise _IndeterminateLockState(
            "A1-M1 indeterminate lock state: the agent slug reappeared after removal."
        )


def _finalize_recovery(
    slug: str, path: Path, expected: dict, expected_hash: str, *, cause: BaseException | None
) -> None:
    """Run the atomic compare-and-remove recovery, then ALWAYS raise. A foreign
    entry maps to a concurrent-update failure (never a removal); a confirmed-absent
    slug maps to the neutral unavailable message; an indeterminate state propagates."""
    try:
        _recover_to_absent(slug, path, expected, expected_hash)
    except _ForeignEntryPresent:
        raise RuntimeError(_CONCURRENT_MSG) from cause
    # _IndeterminateLockState (RuntimeError) propagates unchanged.
    raise RuntimeError(_AGENT_UNAVAILABLE_MSG) from cause


def _commit_agent_entry(slug: str, lock_entry: dict, path: Path) -> None:
    """Durable, sealed final commit bound to the EXACT expected entry.

    Contract (Blocker 1): pre-seal the exact expected entry; upsert ONLY while the
    slug is still absent (never overwrite a foreign re-insert); after the write
    mutation-strict confirm the stored entry equals the exact expected sealed entry
    (structure + entry integrity + ``_integrity.hash`` + the ``python_distribution``
    pair + full sealed-field equality). Any failure drives the slug to absent (never
    restores the old entry); if absence itself cannot be confirmed the state is
    reported as indeterminate — no success, no false 'absent' claim, no swallowing.
    """
    from agentnode_sdk.lock_integrity import seal_entry

    expected = seal_entry(lock_entry)                     # the exact sealed entry
    expected_hash = expected["_integrity"]["hash"]

    def _commit_apply(data: dict) -> bool:
        if slug in data["packages"]:
            raise _ForeignEntryPresent()                 # do NOT overwrite a foreign entry
        data["packages"][slug] = dict(expected)          # write the EXACT expected entry
        return True

    try:
        mutate_lockfile(_commit_apply, path=path, audit_slug=slug, durable=True)
    except _ForeignEntryPresent:
        # a foreign entry was already present at commit — never overwrote it and must
        # never remove it; report the concurrent update, leave the foreign entry.
        raise RuntimeError(_CONCURRENT_MSG)
    except Exception as exc:                              # write/durability/structure failure
        _finalize_recovery(slug, path, expected, expected_hash, cause=exc)  # always raises

    # Mutation-strict confirmation that the EXACT expected entry landed.
    if _stored_matches_expected(read_lockfile_strict(path), slug, expected, expected_hash):
        return
    _finalize_recovery(slug, path, expected, expected_hash, cause=None)     # always raises


def _install_agent_host_transaction(
    slug: str,
    *,
    lock_entry: dict,
    package_dir: Path,
    target_python: str,
    policy: str,
) -> None:
    """Run the A1-M1 host-agent install transaction. Self-committing: on success the
    sealed entry (with the two locally-observed distribution fields) is durably
    written; on any failure the old entry is never restored."""
    from agentnode_sdk import _agent_pip as ap
    from agentnode_sdk import _env_lock as envlock
    from agentnode_sdk import _wheel_meta as wm
    from agentnode_sdk.lock_integrity import validate_python_distribution_fields

    entrypoint = lock_entry.get("entrypoint") or ""
    lf = _lockfile_path()

    # (1) CAS baseline BEFORE the build (first foreign code runs at build).
    baseline_absent, baseline_hash = _capture_cas_baseline(slug, lf)

    dest = Path(tempfile.mkdtemp(prefix="agentnode-wheel-"))
    env_mutating = False
    try:
        # (2) entrypoint top-level (string-only; pre-build fail-closed).
        top = wm.top_level_from_entrypoint(entrypoint)
        # (3) ONE frozen controlled environment drives the config check, the build,
        #     BOTH pip steps, the target-interpreter probes and post-verify — the
        #     build must never re-inherit an unmanaged os.environ. Env identity +
        #     config-redirect refusal + install-scheme (no user-site fallback into a
        #     non-writable/other root) all run BEFORE the build, so a misconfigured or
        #     non-writable target refuses cleanly without running the PEP-517 backend.
        env = ap.controlled_pip_env()
        ident = envlock.resolve_env_identity(target_python, env=env)
        ap.assert_target_contract(target_python, env)
        ap.assert_install_scheme(target_python, env)
        # (4) build-wheel-first (controlled env) + (5) import-free validation.
        wheel = wm.build_wheel(target_python, package_dir, dest, env=env)
        wid = wm.validate_wheel(wheel, top)
        # (6) consistency hash captured before pip hand-off.
        pre_hash = wm.wheel_consistency_hash(wheel)

        with envlock.env_lock(ident.env_id):
            # (8) CAS re-check + cross-slug ownership + name-change + quarantine,
            #     all on the fresh mutation-strict read under the inner file_lock.
            def _quarantine_apply(data: dict) -> bool:
                pkgs = data["packages"]
                current = pkgs.get(slug)
                cur_hash = None
                if isinstance(current, dict):
                    integ = current.get("_integrity")
                    cur_hash = integ.get("hash") if isinstance(integ, dict) else None
                if baseline_absent:
                    if current is not None:
                        raise _CASConflict()
                elif current is None or cur_hash != baseline_hash:
                    raise _CASConflict()
                if isinstance(current, dict):
                    prev_dist = current.get("python_distribution")
                    if prev_dist is not None and prev_dist != wid.distribution:
                        raise _NameChange()
                for oslug, oe in pkgs.items():
                    if oslug == slug or not isinstance(oe, dict):
                        continue
                    if oe.get("package_type") != "agent":
                        continue
                    if wid.distribution and oe.get("python_distribution") == wid.distribution:
                        raise _OwnershipConflict()
                    oep = oe.get("entrypoint") or ""
                    if oep:
                        try:
                            if wm.top_level_from_entrypoint(oep) == top:
                                raise _OwnershipConflict()
                        except wm.WheelValidationError:
                            pass
                if current is None:
                    return False  # new slug: already absent — nothing to write
                pkgs.pop(slug)
                return True

            try:
                mutate_lockfile(_quarantine_apply, path=lf, audit_slug=slug, durable=True)
            except _CASConflict:
                raise _AgentTransactionAbort(
                    "The agent was modified by another install; the stale build was "
                    "not applied."
                )
            except _NameChange:
                raise _AgentTransactionAbort(
                    "The agent's Python distribution name changed; automatic "
                    "migration is not supported."
                )
            except _OwnershipConflict:
                raise _AgentTransactionAbort(
                    "The installed Python distribution or import namespace is already "
                    "owned by another agent."
                )
            except (_AgentTransactionAbort, RuntimeError):
                raise
            except Exception as exc:  # write/durability failure — don't start pip, no restore
                raise _AgentTransactionAbort(_AGENT_UNAVAILABLE_MSG) from exc

            # (9) confirm the slug is absent (mutation-strict) before any pip.
            confirm = read_lockfile_strict(lf)
            if confirm is not None and slug in confirm.get("packages", {}):
                raise _AgentTransactionAbort(_AGENT_UNAVAILABLE_MSG)

            # (10) the validated wheel must be unchanged up to the pip hand-off.
            if wm.wheel_consistency_hash(wheel) != pre_hash:
                raise RuntimeError(_AGENT_UNAVAILABLE_MSG)

            # From here the shared environment is mutated → every failure is neutral.
            env_mutating = True
            ap.pip_install_wheel(target_python, wheel, env)
            ap.post_verify(
                target_python,
                expected_name=wid.distribution,
                expected_version=wid.version,
                expected_top_level=top,
                allowed_roots=[ident.purelib, ident.platlib],
                env=env,
            )

            # (11) durable, sealed commit with the two locally-observed fields.
            lock_entry["build_mode"] = "host"
            lock_entry["effective_host_trust_policy_at_install"] = policy
            lock_entry["pinnable"] = True
            lock_entry["python_distribution"] = wid.distribution
            lock_entry["python_distribution_version"] = wid.version
            validate_python_distribution_fields(lock_entry)
            _commit_agent_entry(slug, lock_entry, lf)
    except (wm.WheelValidationError, ap.AgentPipError) as exc:
        # Pre-quarantine build/metadata/target errors keep the old entry and may be
        # specific; once the environment is mutating, translate to the neutral msg.
        if env_mutating:
            raise RuntimeError(_AGENT_UNAVAILABLE_MSG) from exc
        raise RuntimeError(exc.message) from exc
    except envlock.EnvironmentResolutionError as exc:
        raise RuntimeError("The target Python environment could not be resolved.") from exc
    except _AgentTransactionAbort as exc:
        raise RuntimeError(exc.message) from exc
    finally:
        shutil.rmtree(dest, ignore_errors=True)


# ---------------------------------------------------------------------------
# Publisher signature verification (Phase 16.4)
# ---------------------------------------------------------------------------

def _verify_publisher_signature(
    slug: str,
    lock_entry: dict,
    *,
    key_status: str | None = None,
) -> None:
    """Verify publisher signature on a lock entry before writing.

    - Valid → silent
    - Missing → warn (install continues)
    - Invalid / malformed → raise RuntimeError (install blocked)
    - key_status="revoked" → raise RuntimeError (install blocked)
    """
    import logging
    import warnings

    from agentnode_sdk.signature import verify_entry_signature, SignatureStatus

    result = verify_entry_signature(slug, lock_entry)

    if result.status == SignatureStatus.VALID:
        logging.getLogger(__name__).info(
            "Publisher signature valid for %s (key %s)", slug, result.key_id,
        )
        if key_status == "revoked":
            raise RuntimeError(
                f"Publisher key for '{slug}' has been revoked by the registry"
            )
        return

    if result.status == SignatureStatus.MISSING:
        warnings.warn(
            f"Package '{slug}' has no publisher signature",
            stacklevel=3,
        )
        return

    raise RuntimeError(
        f"Publisher signature verification failed for '{slug}': "
        f"{result.error or result.status.value}"
    )


# ---------------------------------------------------------------------------
# Full install flow
# ---------------------------------------------------------------------------

# P0.0 + 0.18.0 host-trust policy: which tiers may run build hooks (setup.py /
# PEP-517) natively on the host is decided by sandbox.policy.host_allowed_tiers()
# under the active sandbox.host_trust_policy. Every non-host tier builds INSIDE the
# container into a deterministic sealed volume (fail-closed if no runtime — never a
# host build). Artifacts are source trees (no wheel), so this is a fail-closed gate.


def install_package(
    slug: str,
    version: str,
    artifact_url: str | None,
    artifact_hash: str | None = None,
    entrypoint: str | None = None,
    package_type: str = "toolpack",
    capability_ids: list[str] | None = None,
    tools: list[dict[str, str]] | None = None,
    verbose: bool = False,
    trust_level: str | None = None,
    permissions: dict | None = None,
    runtime: str = "python",
    mcp_command: list[str] | None = None,
    remote_endpoint: str | None = None,
    # ANP v0.3 taxonomy fields
    prompts: list[dict] | None = None,
    resources: list[dict] | None = None,
    connector: dict | None = None,
    agent: dict | None = None,
    # Phase 16.4: publisher signatures
    signatures: dict | None = None,
    # Phase 16.6: publisher identity
    publisher_slug: str | None = None,
    # MCP env keys (declared by manifest, stored for runtime UX)
    mcp_env_keys: list[str] | None = None,
    # Stage 4A: optional MCP pre-install descriptor {manager, package, version}
    mcp_install: dict | None = None,
    # Stage 5: optional publisher-declared egress allowlist (mcp_server.allowed_domains)
    mcp_allowed_domains: list | None = None,
    # TG-2: registry-reported key status (install-time revocation)
    key_status: str | None = None,
    # Toolpack runtime credentials: [{name, required, description}] — names
    # only, never values. Sealed into the lockfile for runtime UX + gating.
    env_requirements: list[dict] | None = None,
) -> dict[str, Any]:
    """Execute the full local install flow (mirrors CLI §13.4).

    1. Check lockfile (skip if same version already installed)
    2. Download artifact
    3. Verify SHA256 hash
    4. Extract & validate archive
    5. Verify entrypoint (non-fatal)
    6. Resolve Python interpreter
    7. pip install
    8. Update lockfile
    9. Cleanup

    Returns dict with install result.
    """
    # MCP packages are metadata-only: no artifact, no pip install.
    # Write lockfile entry with mcp_command and return early.
    if runtime == "mcp":
        return _install_mcp(
            slug=slug,
            version=version,
            package_type=package_type,
            mcp_command=mcp_command,
            mcp_env_keys=mcp_env_keys,
            mcp_install=mcp_install,
            mcp_allowed_domains=mcp_allowed_domains,
            capability_ids=capability_ids,
            tools=tools,
            trust_level=trust_level,
            permissions=permissions,
            prompts=prompts,
            resources=resources,
            connector=connector,
            agent=agent,
            signatures=signatures,
            publisher_slug=publisher_slug,
            key_status=key_status,
        )

    if not artifact_url:
        raise RuntimeError(
            f"No artifact available for {slug}@{version}. "
            "The package may be metadata-only."
        )

    # Step 1: Check lockfile
    status = check_installed(slug, version)
    if status == "same":
        return {
            "slug": slug,
            "version": version,
            "installed": True,
            "already_installed": True,
            "message": f"{slug}@{version} is already installed.",
        }

    # Canonicalize publisher_slug (registry-normalized, write-once at install time)
    if publisher_slug:
        publisher_slug = publisher_slug.strip().lower()
        if not publisher_slug:
            publisher_slug = None

    previous_version = None
    if status == "different":
        data = read_lockfile()
        previous_version = data["packages"][slug].get("version")

    # Skills use a different install path: extract to ~/.agentnode/skills/{slug}/
    if package_type == "skill":
        return _install_skill(
            slug=slug,
            version=version,
            artifact_url=artifact_url,
            artifact_hash=artifact_hash,
            previous_version=previous_version if status == "different" else None,
            trust_level=trust_level,
            permissions=permissions,
            prompts=prompts,
            resources=resources,
            assets=None,
            signatures=signatures,
            publisher_slug=publisher_slug,
            key_status=key_status,
        )

    # P0.0/P0.3: building a package runs its setup.py / PEP-517 hooks as arbitrary
    # code. curated/trusted may build natively on the host (vetted tiers); every
    # other tier (community/unverified/unknown) is built INSIDE the container into a
    # deterministic per-pack-version volume (P0.3) — never on the host. The branch
    # is taken at the build step below, after the hash and signature gates pass.
    tmpdir = Path(tempfile.mkdtemp(prefix="agentnode-"))
    try:
        tar_path = tmpdir / "package.tar.gz"
        extract_dir = tmpdir / "extracted"
        extract_dir.mkdir()

        # Step 2: Download
        download_artifact(artifact_url, tar_path)

        # Step 3: Verify hash (P0.0: a registry artifact MUST carry a hash —
        # verify_hash() no-ops on an empty expected value, so require it here)
        if not artifact_hash:
            raise RuntimeError(
                f"Refusing to install '{slug}@{version}': the registry provided no "
                f"artifact hash, so integrity cannot be verified."
            )
        local_hash = verify_hash(tar_path, artifact_hash)

        # Step 4: Extract & validate
        package_dir = extract_archive(tar_path, extract_dir)

        # Step 5: Verify entrypoint (non-fatal)
        _verify_entrypoint(package_dir, entrypoint)

        # Step 6: Resolve Python
        python = resolve_python()

        # Build the lock entry and verify the publisher signature BEFORE pip.
        # pip executes the package's build hooks, so every crypto/trust gate must
        # pass first (P0.0 verify-before-build).
        lock_entry: dict[str, Any] = {
            "version": version,
            "package_type": package_type,
            "runtime": runtime,
            "entrypoint": entrypoint or "",
            "capability_ids": capability_ids or [],
            "tools": tools or [],
            "artifact_hash": f"sha256:{local_hash}",
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "source": "sdk",
            "trust_level": trust_level,
            "last_trust_check": datetime.now(timezone.utc).isoformat(),
            "permissions": permissions,
            # ANP v0.3 taxonomy fields
            "prompts": prompts or [],
            "resources": resources or [],
            "connector": connector,
            "agent": agent,
            # Phase 16.6: publisher identity (Entry-Level, authoritative)
            "publisher_slug": publisher_slug,
        }
        if mcp_command:
            lock_entry["mcp_command"] = mcp_command
        if mcp_env_keys:
            lock_entry["mcp_env_keys"] = mcp_env_keys
        if env_requirements:
            lock_entry["env_requirements"] = env_requirements
        if remote_endpoint:
            lock_entry["remote_endpoint"] = remote_endpoint

        if signatures:
            if publisher_slug:
                for sig in (signatures.get("publisher") or []):
                    if isinstance(sig, dict):
                        sig["publisher_slug"] = publisher_slug
            lock_entry["_signatures"] = signatures
        _verify_publisher_signature(slug, lock_entry, key_status=key_status)

        # Step 7: build — only reached after every gate (hash + signature) passed.
        # Host-allowed tiers (per the active host-trust policy) build natively on the
        # host; every other tier is built INSIDE the container into a deterministic
        # volume (fail-closed if no runtime — never a host build for community code).
        from agentnode_sdk.config import host_trust_policy
        from agentnode_sdk.sandbox.policy import host_allowed_tiers
        policy = host_trust_policy()
        committed = False
        if (trust_level or "").lower() in host_allowed_tiers(policy):
            if package_type == "agent":
                # A1-M1: host agents use the build-wheel-first, environment-locked,
                # CAS-guarded, durable transaction — which self-commits the sealed
                # entry (incl. the two locally-observed distribution fields).
                _install_agent_host_transaction(
                    slug,
                    lock_entry=lock_entry,
                    package_dir=package_dir,
                    target_python=python,
                    policy=policy,
                )
                committed = True
            else:
                pip_install(python, package_dir, verbose=verbose)
                lock_entry["build_mode"] = "host"
        else:
            sandbox_volume = _container_build_into_volume(
                slug, version, package_dir, f"sha256:{local_hash}",
            )
            lock_entry["sandboxed"] = True
            lock_entry["sandbox_volume"] = sandbox_volume
            lock_entry["build_mode"] = "sandbox_volume"

        # MUTABLE metadata (NOT sealed): lets the doctor tell "host-built under an
        # older/looser policy" apart from "sandboxed", without guessing. Toolpacks
        # always build from the pinned artifact, so they are always pinnable.
        # (The agent transaction sets these + commits internally.)
        if not committed:
            lock_entry["effective_host_trust_policy_at_install"] = policy
            lock_entry["pinnable"] = True

            # Step 8: seal + write lockfile (only after a successful install)
            from agentnode_sdk.lock_integrity import seal_entry
            lock_entry = seal_entry(lock_entry)
            update_lockfile(slug, lock_entry)

        result: dict[str, Any] = {
            "slug": slug,
            "version": version,
            "installed": True,
            "already_installed": False,
            "hash_verified": bool(artifact_hash),
            "entrypoint": entrypoint,
            "lockfile_updated": True,
        }
        if previous_version:
            result["previous_version"] = previous_version
            result["message"] = f"Upgraded {slug} from {previous_version} to {version}."
        else:
            result["message"] = f"Installed {slug}@{version}."

        return result

    finally:
        # Step 9: Cleanup
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# MCP install (metadata-only, no artifact)
# ---------------------------------------------------------------------------


def _install_mcp(
    slug: str,
    version: str,
    package_type: str = "toolpack",
    mcp_command: list[str] | None = None,
    mcp_env_keys: list[str] | None = None,
    mcp_install: dict | None = None,
    mcp_allowed_domains: list | None = None,
    capability_ids: list[str] | None = None,
    tools: list[dict[str, str]] | None = None,
    trust_level: str | None = None,
    permissions: dict | None = None,
    prompts: list[dict] | None = None,
    resources: list[dict] | None = None,
    connector: dict | None = None,
    agent: dict | None = None,
    signatures: dict | None = None,
    publisher_slug: str | None = None,
    key_status: str | None = None,
) -> dict[str, Any]:
    """Install an MCP package (metadata-only, unless an mcp_install descriptor opts
    into Stage 4A pre-install into a sealed volume; Stage 5 also seals the
    publisher-declared egress allowlist as mcp_allowed_domains)."""
    if not mcp_command:
        raise RuntimeError(
            f"MCP package {slug}@{version} has no mcp_command. "
            "Cannot install without a command to run."
        )

    status = check_installed(slug, version)
    if status == "same":
        return {
            "slug": slug,
            "version": version,
            "installed": True,
            "already_installed": True,
            "message": f"{slug}@{version} is already installed.",
        }

    if publisher_slug:
        publisher_slug = publisher_slug.strip().lower()
        if not publisher_slug:
            publisher_slug = None

    previous_version = None
    if status == "different":
        data = read_lockfile()
        previous_version = data["packages"][slug].get("version")

    lock_entry: dict[str, Any] = {
        "version": version,
        "package_type": package_type,
        "runtime": "mcp",
        "entrypoint": "",
        "capability_ids": capability_ids or [],
        "tools": tools or [],
        "artifact_hash": "",
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "source": "sdk",
        "trust_level": trust_level,
        "last_trust_check": datetime.now(timezone.utc).isoformat(),
        "permissions": permissions,
        "prompts": prompts or [],
        "resources": resources or [],
        "connector": connector,
        "agent": agent,
        "publisher_slug": publisher_slug,
        "mcp_command": mcp_command,
    }
    if mcp_env_keys:
        lock_entry["mcp_env_keys"] = mcp_env_keys

    if signatures:
        if publisher_slug:
            for sig in (signatures.get("publisher") or []):
                if isinstance(sig, dict):
                    sig["publisher_slug"] = publisher_slug
        lock_entry["_signatures"] = signatures
    _verify_publisher_signature(slug, lock_entry, key_status=key_status)

    # Stage 4A: optional pre-install into a sealed volume — runs AFTER the publisher
    # signature/policy gate (verify-before-build), mirroring the toolpack path. An
    # invalid or blocked signature raises above, so NO registry fetch / build / volume
    # write ever happens for it. `mcp_install` is the ONLY source (mcp_command is NEVER
    # parsed). Absent -> metadata-only (no preinstall fields). Present-but-invalid ->
    # raise before any build. The existing `mcp_command` is left UNCHANGED; the resolved
    # local entrypoint goes into the NEW, run-path-unused `mcp_preinstall_command`.
    if mcp_install is not None:
        mgr, pkg, pkg_ver = validate_mcp_install(mcp_install)
        volume, tree_hash, preinstall_command = _container_build_mcp_volume(
            slug, version, mgr, pkg, pkg_ver,
        )
        mcp_preinstall = {
            "manager": mgr,
            "package": pkg,
            "version": pkg_ver,
            "artifact_hash": tree_hash,
        }
        # Stage 5: the sealed descriptor MUST be exactly the validated mcp_install — the
        # volume + run-time gates are bound to it. Defensive (built from the same source);
        # any divergence is fail-closed, nothing written.
        if (mcp_preinstall["manager"], mcp_preinstall["package"], mcp_preinstall["version"]) \
                != (mgr, pkg, pkg_ver):
            raise RuntimeError(
                f"mcp_preinstall descriptor does not match mcp_install for '{slug}@{version}'"
            )
        lock_entry["mcp_preinstalled"] = True
        lock_entry["mcp_preinstall"] = mcp_preinstall
        lock_entry["mcp_sandbox_volume"] = volume
        lock_entry["mcp_preinstall_command"] = preinstall_command
        # Stage 5: seal the publisher-declared egress allowlist (canonicalized). NOT
        # consumed at run time yet (that is Stage 3B); sealed so 3B trusts it.
        if mcp_allowed_domains is not None:
            from agentnode_sdk.sandbox.domain_policy import canonicalize_allowed_domains
            lock_entry["mcp_allowed_domains"] = list(
                canonicalize_allowed_domains(mcp_allowed_domains)
            )

    # MUTABLE metadata (NOT sealed) for the doctor: an MCP is "pinnable" only if it
    # ships an mcp_install descriptor (→ a sealed volume was built). A floating
    # command-only MCP is NOT pinnable and, under a stricter host-trust policy, is
    # refused at run time — the user cannot fix that (the publisher must pin it),
    # which the doctor distinguishes from a merely-missing volume.
    from agentnode_sdk.config import host_trust_policy
    lock_entry["pinnable"] = mcp_install is not None
    lock_entry["build_mode"] = "sandbox_volume" if mcp_install is not None else "host"
    lock_entry["effective_host_trust_policy_at_install"] = host_trust_policy()

    from agentnode_sdk.lock_integrity import seal_entry
    lock_entry = seal_entry(lock_entry)
    update_lockfile(slug, lock_entry)

    result: dict[str, Any] = {
        "slug": slug,
        "version": version,
        "installed": True,
        "already_installed": False,
        "hash_verified": False,
        "entrypoint": None,
        "lockfile_updated": True,
    }
    if previous_version:
        result["previous_version"] = previous_version
        result["message"] = f"Upgraded {slug} from {previous_version} to {version}."
    else:
        result["message"] = f"Installed {slug}@{version}."

    return result


# ---------------------------------------------------------------------------
# Skill install (filesystem-first, no pip)
# ---------------------------------------------------------------------------

SKILL_ALLOWED_EXTENSIONS = frozenset({
    ".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".xml",
    ".html", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp",
})

SKILL_FORBIDDEN_FILES = frozenset({
    "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "makefile", "dockerfile", "cargo.toml", "go.mod", "go.sum",
    "gemfile", "rakefile", "cmakelists.txt", "meson.build",
    "requirements.txt", "pipfile", "poetry.lock",
})


def _skills_dir() -> Path:
    """Return the global skills directory (~/.agentnode/skills/)."""
    from agentnode_sdk.config import config_dir
    return config_dir() / "skills"


def _validate_skill_contents(package_dir: Path, manifest: dict) -> list[str]:
    """Validate extracted skill directory contains only allowed files.

    Returns list of errors. Empty list means valid.
    """
    declared_assets: set[str] = set()
    for asset in manifest.get("assets", []):
        if isinstance(asset, dict) and asset.get("path"):
            declared_assets.add(asset["path"])
    caps = manifest.get("capabilities", {})
    for prompt in caps.get("prompts", []) if isinstance(caps, dict) else []:
        if isinstance(prompt, dict) and prompt.get("template"):
            declared_assets.add(prompt["template"])

    allowed = {"agentnode.yaml", "SKILL.md"} | declared_assets
    errors: list[str] = []

    for item in package_dir.rglob("*"):
        if item.is_dir():
            continue
        rel = str(item.relative_to(package_dir)).replace("\\", "/")

        if rel not in allowed:
            errors.append(f"Undeclared file '{rel}' in skill artifact")
            continue

        basename = item.name.lower()
        if basename in SKILL_FORBIDDEN_FILES:
            errors.append(f"Forbidden build/runtime file '{rel}' in skill artifact")
            continue

        ext = item.suffix.lower()
        if ext and ext not in SKILL_ALLOWED_EXTENSIONS:
            errors.append(
                f"File '{rel}' has forbidden extension '{ext}'. "
                f"Allowed: {sorted(SKILL_ALLOWED_EXTENSIONS)}"
            )

    return errors


def _install_skill(
    slug: str,
    version: str,
    artifact_url: str,
    artifact_hash: str | None,
    previous_version: str | None,
    trust_level: str | None,
    permissions: dict | None,
    prompts: list[dict] | None,
    resources: list[dict] | None,
    assets: list[dict] | None,
    signatures: dict | None = None,
    publisher_slug: str | None = None,
    key_status: str | None = None,
) -> dict[str, Any]:
    """Install a skill package to ~/.agentnode/skills/{slug}/.

    No pip install, no Python runtime. Just extract and place files.
    Validates contents after extraction — aborts without touching
    the existing install if validation fails.
    """
    dest = _skills_dir() / slug
    tmpdir = Path(tempfile.mkdtemp(prefix="agentnode-skill-"))

    try:
        tar_path = tmpdir / "package.tar.gz"
        extract_dir = tmpdir / "extracted"
        extract_dir.mkdir()

        download_artifact(artifact_url, tar_path)
        local_hash = verify_hash(tar_path, artifact_hash)
        package_dir = extract_archive(tar_path, extract_dir)

        # Read manifest from extracted package for lockfile metadata
        manifest_assets = assets or []
        manifest_prompts = prompts or []

        manifest_path = package_dir / "agentnode.yaml"
        manifest: dict = {}
        if manifest_path.is_file():
            import yaml
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                manifest = {}
            if not manifest_prompts:
                caps = manifest.get("capabilities", {})
                manifest_prompts = caps.get("prompts", [])
            if not manifest_assets:
                manifest_assets = manifest.get("assets", [])

        # Revalidate extracted contents — abort if invalid
        content_errors = _validate_skill_contents(package_dir, manifest)
        if content_errors:
            raise RuntimeError(
                f"Skill '{slug}' contains forbidden files — install aborted. "
                f"Errors: {'; '.join(content_errors[:5])}"
            )

        # Atomic replace: build new dir in temp, then swap
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging = dest.parent / f".{slug}_staging"
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(package_dir, staging)

        old_backup = dest.parent / f".{slug}_old"
        if old_backup.exists():
            shutil.rmtree(old_backup)

        try:
            if dest.exists():
                dest.rename(old_backup)
            staging.rename(dest)
        except Exception:
            # Rollback: restore old version if rename failed
            if old_backup.exists() and not dest.exists():
                old_backup.rename(dest)
            raise

        # Clean up old backup
        if old_backup.exists():
            shutil.rmtree(old_backup, ignore_errors=True)

        lock_entry: dict[str, Any] = {
            "version": version,
            "package_type": "skill",
            "runtime": "none",
            "install_mode": "prompt_only",
            "entrypoint": "",
            "capability_ids": [],
            "tools": [],
            "artifact_hash": f"sha256:{local_hash}",
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "install_path": str(dest),
            "source": "sdk",
            "trust_level": trust_level,
            "permissions": permissions,
            "prompts": manifest_prompts,
            "resources": resources or [],
            "assets": manifest_assets,
            "connector": None,
            "agent": None,
            "publisher_slug": publisher_slug,
        }

        if signatures:
            if publisher_slug:
                for sig in (signatures.get("publisher") or []):
                    if isinstance(sig, dict):
                        sig["publisher_slug"] = publisher_slug
            lock_entry["_signatures"] = signatures
        _verify_publisher_signature(slug, lock_entry, key_status=key_status)

        from agentnode_sdk.lock_integrity import seal_entry
        lock_entry = seal_entry(lock_entry)
        update_lockfile(slug, lock_entry)

        result: dict[str, Any] = {
            "slug": slug,
            "version": version,
            "installed": True,
            "already_installed": False,
            "hash_verified": bool(artifact_hash),
            "install_path": str(dest),
            "lockfile_updated": True,
        }
        if previous_version:
            result["previous_version"] = previous_version
            result["message"] = f"Upgraded {slug} from {previous_version} to {version}."
        else:
            result["message"] = f"Installed skill {slug}@{version}."

        return result

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def remove_skill_directory(slug: str) -> bool:
    """Delete the skill directory for a given slug. Returns True if deleted."""
    dest = _skills_dir() / slug
    if dest.is_dir():
        shutil.rmtree(dest)
        return True
    return False


# ---------------------------------------------------------------------------
# Load installed tool
# ---------------------------------------------------------------------------

def _resolve_entrypoint(entrypoint: str) -> tuple[str, str]:
    """Parse an entrypoint string into (module_path, function_name).

    Supports both v0.1 and v0.2 formats:
      "my_pack.tool"           → ("my_pack.tool", "run")
      "my_pack.tool:describe"  → ("my_pack.tool", "describe")
    """
    if ":" in entrypoint:
        module_path, func_name = entrypoint.rsplit(":", 1)
        return module_path, func_name
    return entrypoint, "run"


def _default_tool_entrypoint(entry: dict) -> str | None:
    """Entrypoint to use when no explicit ``tool_name`` is given.

    Auto-selects the sole tool when the pack declares EXACTLY one tool with an
    entrypoint, so ``agentnode run <slug>`` works for single-tool packs without
    the caller knowing the tool name. Otherwise falls back to the package-level
    entrypoint — multi-tool packs are unchanged (the runner must not guess
    among several tools). Returns ``None`` when neither is available.
    """
    tools = entry.get("tools") or []
    if len(tools) == 1 and tools[0].get("entrypoint"):
        return tools[0]["entrypoint"]
    return entry.get("entrypoint")


def _multi_tool_hint(entry: dict, slug: str = "<slug>") -> str:
    """Trailing hint for resolution-failure errors: if the pack exposes MORE than
    one tool, point the user at ``agentnode run <slug>:<tool>`` and list the tools.
    Empty string for single-tool / no-tools packs. Message-only — does not change
    which entrypoint is chosen."""
    names = [t.get("name") for t in (entry.get("tools") or []) if t.get("name")]
    if len(names) > 1:
        return (
            f" This package exposes multiple tools: {names}. "
            f"Run a specific one with: agentnode run {slug}:<tool>."
        )
    return ""


def _resolve_entrypoint_from_entry(
    entry: dict, slug: str, tool_name: str | None
) -> tuple[str, list[str]]:
    """Resolve ``(module_path, [ordered_function_candidates])`` from a lockfile
    entry — STRING-ONLY, no import, no lockfile read, no policy. Mirrors the
    historical ``load_tool`` precedence exactly:
      - v0.2 per-tool entrypoint match → that module + its function;
      - v0.1 (tool_name given, no ``tools`` list) → package module, try
        ``tool_name`` first then the default function (``run``);
      - no tool_name → the default/sole-tool/package entrypoint module + function.
    Raises :class:`ImportError` (controlled, message-only) when unresolvable.
    """
    tools = entry.get("tools") or []
    if tool_name:
        for t in tools:
            if t.get("name") == tool_name and t.get("entrypoint"):
                module_path, func_name = _resolve_entrypoint(t["entrypoint"])
                return module_path, [func_name]
        entrypoint = entry.get("entrypoint")
        if entrypoint and not tools:
            module_path, func_name = _resolve_entrypoint(entrypoint)
            cands = [tool_name] if tool_name == func_name else [tool_name, func_name]
            return module_path, cands
        raise ImportError(
            f"Tool '{tool_name}' not found in package '{slug}'. "
            f"Available tools: {[t.get('name') for t in tools]}"
        )

    entrypoint = _default_tool_entrypoint(entry)
    if not entrypoint:
        raise ImportError(
            f"Package '{slug}' has no entrypoint in lockfile." + _multi_tool_hint(entry, slug)
        )
    module_path, func_name = _resolve_entrypoint(entrypoint)
    return module_path, [func_name]


def _load_entrypoint_from_entry(entry: dict, slug: str, tool_name: str | None) -> Any:
    """Private, POLICY-FREE loader: resolve the entrypoint from *entry*, import the
    module, and return the first callable candidate. Reads NO lockfile, runs NO
    integrity gate / warning / audit / trust-refresh / mutation / network — only
    the string resolution above then ``importlib``. Callers that need an integrity
    decision (public :func:`load_tool`) gate BEFORE calling this."""
    module_path, candidates = _resolve_entrypoint_from_entry(entry, slug, tool_name)
    mod = _import_module(module_path, slug)          # first code execution (module import)
    for name in candidates:
        func = getattr(mod, name, None)
        if func is not None and callable(func):
            return func
    raise ImportError(
        f"Function {candidates!r} not found in module '{module_path}' "
        f"for package '{slug}'." + _multi_tool_hint(entry, slug)
    )


def load_tool(slug: str, tool_name: str | None = None, *, _internal: bool = False) -> Any:
    """Load an installed package's tool function (Direct Mode).

    Two-stage lockfile integrity is enforced BEFORE any module import — the same
    merged core decision as ``run_tool`` (0.2A-2c), NOT a second policy. Order:
    policy-bypass warning (unless ``_internal``) → ONE fail-closed snapshot →
    integrity gate (unconditional) → integrity warning/audit on a normal-mode
    migration → resolve the entry from the SAME snapshot → import.

    ``_internal`` ONLY suppresses the policy-bypass warning; it NEVER affects the
    integrity gate (a leading underscore is not a security boundary). The gate runs
    on every public call even if the target module is already cached in
    ``sys.modules`` (a cached module must not become a bypass).

    Returns the callable tool function. For v0.1 packs: ``module.run``; for v0.2
    packs with ``tool_name``: the specific tool function.
    """
    if not _internal:
        import warnings
        warnings.warn(
            "load_tool() bypasses policy checks. Use run_tool() for safe execution.",
            RuntimeWarning,
            stacklevel=2,
        )

    # Fail-closed snapshot + two-stage integrity gate, BEFORE any import. Lazy
    # import avoids an installer<->runtime_integrity cycle and keeps installer free
    # of any runner import.
    from agentnode_sdk.guard import _is_strict
    from agentnode_sdk.lock_integrity import LockIntegrityDenied
    from agentnode_sdk.lock_surface import audit_lock_integrity, warn_lock_integrity
    from agentnode_sdk.runtime_integrity import gate_lock_integrity

    try:
        _lock, entry, report = gate_lock_integrity(slug, None, strict=_is_strict())
    except LockIntegrityDenied as denied:
        audit_lock_integrity(denied.report, slug)
        if denied.report.entry_status == "absent":
            # Preserve the historical "not installed" contract as a neutral ImportError.
            raise ImportError(
                f"Package '{slug}' is not installed. Install it first: client.install('{slug}')"
            ) from None
        raise
    # LockfileFormatError (unreadable / parser / base-model) propagates uncaught —
    # a hard read error is never disguised as "not installed", and no import runs.

    # Allowed but not fully verified → exactly one integrity warning + one allow
    # audit (normal mode). Strict would already have denied above; verified/verified
    # is silent. This is SEPARATE from the policy-bypass warning above.
    if report.reason != "verified":
        warn_lock_integrity(slug, report)
        audit_lock_integrity(report, slug)

    return _load_entrypoint_from_entry(entry, slug, tool_name)


def _import_module(module_path: str, slug: str) -> Any:
    """Import a Python module by dotted path."""
    try:
        return importlib.import_module(module_path)
    except ImportError:
        raise ImportError(
            f"Could not import '{module_path}' for package '{slug}'. "
            "The package may need to be reinstalled."
        )
