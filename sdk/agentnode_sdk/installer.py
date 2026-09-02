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
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from agentnode_sdk.exceptions import AgentNodeError, LockfileFormatError

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

class InterpreterResolutionError(AgentNodeError):
    """The target Python interpreter could not be uniquely, canonically resolved —
    fail-closed (no env-lock, no host mutation). The env-lock identity is derived from
    the interpreter, so a non-canonical / ambiguous interpreter must never proceed.

    It is an ``AgentNodeError`` so the stable ``code`` (``interpreter_not_resolvable``)
    surfaces structurally: raised in the host-mutation branch, it propagates uncaught
    through ``install_package`` and ``client.install`` and is rendered traceback-free by
    the CLI top-level handler as ``Error: [interpreter_not_resolvable] <message>``."""

    code = "interpreter_not_resolvable"

    def __init__(
        self,
        message: str = "The target Python interpreter could not be canonically resolved.",
    ):
        super().__init__(self.code, message)


class EnvironmentIdentityChanged(AgentNodeError):
    """The target environment's identity changed between the moment it was resolved and
    the moment the environment write-lock was acquired (interpreter replaced / install
    roots moved). Fail-closed under the lock BEFORE any mutation — an install bound to
    the old identity must never mutate a different environment. Stable ``code`` surfaces
    traceback-free through the CLI."""

    code = "environment_identity_changed"

    def __init__(self, message: str = "The target Python environment changed during install."):
        super().__init__(self.code, message)


class PreparedInstallStale(AgentNodeError):
    """The artifact prepared before the write-lock no longer matches the stabilised state
    observed under the lock (the built wheel changed on disk, or its digest no longer
    matches the prepared digest). Fail-closed BEFORE any new pip/quarantine mutation — no
    automatic rebuild inside the write-lock. Stable ``code`` surfaces traceback-free."""

    code = "prepared_install_stale"

    def __init__(self, message: str = "The prepared install artifact is stale; reinstall."):
        super().__init__(self.code, message)


class PreparedInstallInvalid(AgentNodeError):
    """The lock entry bound at Phase A is not a canonically-serializable authorization: it
    contains a value that is not a JSON-safe type (only None / bool / int / finite float /
    str / list / dict-with-string-keys are permitted). Fail-closed BEFORE the environment
    lock — a non-canonical authorization is never silently coerced (no ``default=str``)."""

    code = "prepared_install_invalid"

    def __init__(self, message: str = "The install authorization is not canonically serializable."):
        super().__init__(self.code, message)


# The infrastructure-failure code for the environment write-lock (counter / ticket /
# queue-mutex / OS-lock / directory failure). The timeout / read->write-upgrade / nested
# codes are single-sourced on the corresponding ``_env_rwlock`` exception ``.code``.
ENVIRONMENT_WRITE_LOCK_FAILED = "environment_write_lock_failed"


class EnvironmentWriteLockError(AgentNodeError):
    """A structured, fail-closed environment write-lock error surfaced UNIFORMLY for both
    host routes (agent + toolpack). Raised only during acquisition — BEFORE any host
    mutation — so it never leaves the environment half-changed. Stable ``code`` (one of
    ``environment_write_lock_timeout`` / ``read_to_write_upgrade_forbidden`` /
    ``environment_write_lock_nested`` / ``environment_write_lock_failed``) surfaces
    traceback-free through install_package / client.install / CLI / doctor."""

    def __init__(self, code: str, message: str):
        super().__init__(code, message)


class ToolpackPublishFailed(AgentNodeError):
    """A host toolpack's pip build+install SUCCEEDED but the lockfile publish then failed:
    the environment is changed while no matching lockfile entry exists. Fail-closed and
    REPARABLE — the package is on the host but unrecorded, so re-running the install (pip
    is idempotent for the same version) records it. Raised under the write-lock; the lock
    is released only after this defined state is surfaced (never silently)."""

    code = "toolpack_publish_failed"

    def __init__(
        self,
        message: str = (
            "The package was installed but the lockfile could not be updated. Re-run "
            "the install to record it."
        ),
    ):
        super().__init__(self.code, message)


class ToolpackCommitConflict(AgentNodeError):
    """After a host toolpack's pip step, the final compare-and-add commit found the slug
    already (re)published by ANOTHER path (a container install, an install into a different
    target environment, or any path not serialized by THIS environment write-lock). The
    concurrent entry is NEVER overwritten and NEVER removed; no success is reported. Because
    pip may already have changed the target environment, explicit cleanup or reinstallation
    may be required."""

    code = "toolpack_commit_conflict_after_pip"

    def __init__(
        self,
        message: str = (
            "Another install recorded this package concurrently; the target environment may "
            "have been changed. The concurrent entry was left intact — reinstall to reconcile."
        ),
    ):
        super().__init__(self.code, message)


class ToolpackPreparationStale(AgentNodeError):
    """Between the START of this host-toolpack install (when its same-slug baseline was
    bound, BEFORE the lengthy download/extract) and acquiring the environment write-lock,
    the slug's lockfile state changed — a NEWER install of the same package completed
    concurrently. Fail-closed BEFORE any mutation (no quarantine, no pip): the newer entry
    is left byte-identical. This is distinct from a post-pip commit conflict because the
    environment has NOT been touched yet."""

    code = "toolpack_preparation_stale"

    def __init__(
        self,
        message: str = (
            "This install was superseded by a newer install of the same package during its "
            "preparation; nothing was changed. Re-run the install if it is still wanted."
        ),
    ):
        super().__init__(self.code, message)


class InstallStartSnapshotInvalid(AgentNodeError):
    """The install-start snapshot passed into ``install_package`` is inconsistent with the
    call (a different slug, or a missing/ill-formed field). Fail-closed BEFORE any mutation —
    a snapshot that does not authorize THIS exact install is never silently re-captured."""

    code = "install_start_snapshot_invalid"

    def __init__(self, message: str = "The install-start snapshot does not match this install."):
        super().__init__(self.code, message)


class HostPolicyChangedDuringInstall(AgentNodeError):
    """The active host-trust policy changed between the START of this install (when the route
    was decided from the bound snapshot) and the first host mutation under the environment
    write-lock. Fail-closed (no quarantine, no pip, no publish): a policy change requires a
    NEW install — an operation is never silently redirected host↔container mid-flight."""

    code = "host_policy_changed_during_install"

    def __init__(
        self,
        message: str = (
            "The host-trust policy changed during this install; nothing was changed. Re-run "
            "the install under the current policy."
        ),
    ):
        super().__init__(self.code, message)


# One single monotone total budget bounds the environment write-lock acquisition. On
# timeout NOTHING is mutated (the lock is not held → no recovery, quarantine, pip, verify,
# lockfile write, or foreign-state cleanup runs).
ENV_WRITE_LOCK_TIMEOUT = 300.0


def _normalize_interpreter_launch_path(path: str | None) -> str | None:
    """The SINGLE interpreter-launch-path definition (used by resolve_python, and through it
    the env write-lock identity, pip and post-verify). Return an ABSOLUTE, lexically-
    normalised interpreter LAUNCH path with its FINAL SYMLINK PRESERVED — never a physically
    resolved realpath, and never a bare program name — else None.

    Why the final symlink is preserved: a POSIX venv launcher (``venv/bin/python`` → the base
    interpreter) carries its environment through the launch path + the adjacent
    ``pyvenv.cfg``. Physically resolving it to the base binary would DESTROY that binding —
    pip would install into, and post-verify would inspect, the BASE environment instead of
    the requested venv, and two independent venvs sharing one base binary would collapse to
    one env-id. So we bind + normalise lexically only (expanduser, abspath against the current
    CWD, normpath) and check the launch path exists and is an executable Python 3, WITHOUT
    dereferencing its final symlink. Semantic identity is still derived by EXECUTING this
    launch path in ``resolve_env_identity`` — two launchers of the SAME environment therefore
    still resolve to the SAME env-id, while two genuinely different venvs do not."""
    if not path:
        return None
    launch = os.path.abspath(os.path.normpath(os.path.expanduser(path)))
    try:
        if not os.path.isfile(launch):           # exists + regular file (final symlink target)
            return None
    except OSError:
        return None
    if not _is_python3(launch):                   # must actually be an executable Python 3
        return None
    return launch


def resolve_python(explicit: str | None = None) -> str:
    """Return the ABSOLUTE, lexically-normalised LAUNCH path of the target Python 3
    interpreter — venv-preserving (its final symlink is NOT physically resolved), and NEVER a
    bare name. Fail-closed (``InterpreterResolutionError``, code ``interpreter_not_resolvable``)
    when it cannot be resolved. This value is load-bearing: the SAME launch path drives the
    environment write-lock identity, pip and post-verify, so an explicit
    ``target_python=/venv/bin/python`` actually targets THAT venv (not its shared base
    interpreter).

    - An EXPLICIT target has UNCONDITIONAL priority (evaluated before sys.executable):
        * a PATH (absolute or relative) → expanded, bound against the CURRENT CWD, lexically
          normalised, existence/executability checked, FINAL SYMLINK PRESERVED;
        * a bare program name (e.g. ``python-custom``) → ``shutil.which`` → the returned
          launcher path, likewise normalised WITHOUT realpath.
    - NO explicit target → ONLY ``sys.executable`` (the interpreter actually running
      AgentNode), absolutised + normalised, symlink preserved. There is deliberately NO
      $VIRTUAL_ENV / ./.venv / PATH fallback: such a fallback could select a DIFFERENT
      environment than the one we run in and lock/build the wrong one. If sys.executable is
      unusable → fail-closed.
    """
    if explicit:
        import shutil
        is_path = (os.path.isabs(explicit) or os.sep in explicit
                   or (os.altsep and os.altsep in explicit))
        if is_path:
            resolved = _normalize_interpreter_launch_path(explicit)      # abs / rel path
        else:
            found = shutil.which(explicit)                               # bare program name
            resolved = _normalize_interpreter_launch_path(found) if found else None
        if resolved:
            return resolved
        raise InterpreterResolutionError(
            f"explicit target interpreter could not be resolved to an executable "
            f"Python 3 launch path: {explicit!r}")

    resolved = _normalize_interpreter_launch_path(sys.executable)
    if resolved:
        return resolved
    raise InterpreterResolutionError(
        "sys.executable is not a resolvable executable Python 3 interpreter")


def _is_python3(path: str) -> bool:
    try:
        out = subprocess.check_output(
            [path, "--version"], stderr=subprocess.STDOUT, timeout=5
        ).decode().strip()
        return out.startswith("Python 3.")
    except Exception:
        return False


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
        # Both supported managers cache under $HOME by default — uv in
        # $HOME/.cache/uv, npm in $HOME/.npm — and the sandbox HOME is a deliberately
        # small 16 MiB tmpfs (container_backend: home_size default "16m"). Installing
        # any package with a real dependency tree therefore exhausted it: the observed
        # failure was uv writing /sandbox-home/.cache/uv/... "No space left on device".
        # Point both caches at the 512 MiB /tmp tmpfs this build already asks for. The
        # paths are inside the container's own /tmp, so nothing is shared between
        # sandboxes, nothing touches a host cache, and no limit or mount grows.
        # XDG_CACHE_HOME is deliberately not used: npm does not honour it.
        env={"UV_CACHE_DIR": "/tmp/uv-cache", "npm_config_cache": "/tmp/npm-cache"},
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

_TOOLPACK_UNAVAILABLE_MSG = (
    "Installation state may have changed. The toolpack is unavailable until it is "
    "reinstalled."
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


class _ToolpackCommitConflict(Exception):
    """Internal signal: the toolpack final compare-and-add found the slug already present
    (a concurrent publish). Translated to the stable ``ToolpackCommitConflict``."""


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


def _same_slug_matches_baseline(current: Any, baseline_absent: bool,
                                baseline_hash: str | None) -> bool:
    """True iff the CURRENT same-slug entry still matches the baseline captured at the start
    of the install (before the lengthy preparation). Baseline-absent → must still be absent;
    baseline-present-with-hash-H → must still be present with integrity hash H."""
    if baseline_absent:
        return current is None
    if not isinstance(current, dict):
        return False
    integ = current.get("_integrity")
    return isinstance(integ, dict) and integ.get("hash") == baseline_hash


@dataclass(frozen=True)
class _InstallStartSnapshot:
    """The ONE context bound at the EARLIEST start of a slug install — before registry /
    catalog resolution, version selection, download, extraction, or build. It anchors the
    whole operation so a concurrently-completed newer install cannot be adopted as this
    operation's baseline, and so the lockfile path and host-trust policy cannot drift from
    process-global state (``AGENTNODE_LOCKFILE`` / CWD / config) mid-transaction.

    Captured by the client at the top of ``AgentNodeClient.install`` (and threaded through the
    CLI / doctor funnels); direct/legacy callers of ``install_package`` without one get it
    captured at entry. ``lockfile_path`` is absolute (bound to the start-time CWD)."""
    slug: str
    lockfile_path: Path               # absolute — bound once, reused by every phase
    baseline_absent: bool
    baseline_integrity_hash: str | None
    host_policy_snapshot: str


def capture_install_start_snapshot(slug: str) -> _InstallStartSnapshot:
    """Bind the same-slug start state ONCE, at the earliest point of an install operation:
    the absolute lockfile path, the host-trust policy, and the same-slug CAS baseline. Must
    be called BEFORE any registry resolution / version selection / download."""
    from agentnode_sdk.config import host_trust_policy

    lf = Path(_lockfile_path()).absolute()          # anchor to the start-time CWD (no re-drift)
    policy = host_trust_policy()
    baseline_absent, baseline_hash = _capture_cas_baseline(slug, lf)
    return _InstallStartSnapshot(
        slug=slug,
        lockfile_path=lf,
        baseline_absent=baseline_absent,
        baseline_integrity_hash=baseline_hash,
        host_policy_snapshot=policy,
    )


def _bind_or_capture_install_snapshot(
    start_snapshot: _InstallStartSnapshot | None, slug: str,
) -> _InstallStartSnapshot:
    """Use the caller-provided snapshot (validated, never silently re-captured) or, for a
    direct/legacy call without one, capture it at entry. A snapshot for a DIFFERENT slug, or
    one with a missing/ill-formed field, is fail-closed (:class:`InstallStartSnapshotInvalid`)."""
    if start_snapshot is None:
        return capture_install_start_snapshot(slug)
    if not isinstance(start_snapshot, _InstallStartSnapshot):
        raise InstallStartSnapshotInvalid("not an install-start snapshot")
    if (
        start_snapshot.slug != slug
        or not isinstance(start_snapshot.lockfile_path, Path)
        or not start_snapshot.lockfile_path.is_absolute()
        or not isinstance(start_snapshot.host_policy_snapshot, str)
        or not start_snapshot.host_policy_snapshot
        or not isinstance(start_snapshot.baseline_absent, bool)
    ):
        raise InstallStartSnapshotInvalid()
    return start_snapshot


def _assert_host_policy_unchanged(snapshot_policy: str) -> None:
    """Fail-closed (:class:`HostPolicyChangedDuringInstall`) unless the CURRENT host-trust
    policy still equals the one bound at the start of the install. Called under the
    environment write-lock, BEFORE the first host mutation — a policy change requires a new
    install (no silent host↔container redirect)."""
    from agentnode_sdk.config import host_trust_policy

    if host_trust_policy() != snapshot_policy:
        raise HostPolicyChangedDuringInstall()


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


def _commit_toolpack_entry(slug: str, lock_entry: dict, path: Path) -> None:
    """Durable, atomic compare-and-ADD final commit for a host toolpack. Seal the entry and
    insert it ONLY while the slug is still absent (exactly as the pre-pip quarantine left it)
    — NEVER overwrite / remove a concurrent entry published by another path (a container
    install, an install into a different target environment, or any path not serialized by
    THIS environment write-lock) while pip ran.

    Concurrent entry present → :class:`ToolpackCommitConflict` (no success, foreign entry
    left byte-identical). A write/durability failure → mutation-strict readback: accept ONLY
    if the exact sealed entry landed; a foreign entry → conflict; nothing → the reparable
    :class:`ToolpackPublishFailed`. Unlike the agent commit, a toolpack conflict NEVER drives
    the slug to absent — that would remove the concurrent winner's entry."""
    from agentnode_sdk.lock_integrity import seal_entry

    expected = seal_entry(lock_entry)                     # the exact sealed entry
    expected_hash = expected["_integrity"]["hash"]

    def _commit_apply(data: dict) -> bool:
        if data["packages"].get(slug) is not None:
            raise _ToolpackCommitConflict()              # do NOT overwrite a concurrent entry
        data["packages"][slug] = dict(expected)          # compare-and-add while still absent
        return True

    try:
        mutate_lockfile(_commit_apply, path=path, audit_slug=slug, durable=True)
    except _ToolpackCommitConflict:
        raise ToolpackCommitConflict()
    except Exception as exc:                              # write/durability failure
        # A possible partial replace happened. Read back mutation-strict and accept ONLY our
        # exact sealed entry; never blindly re-write, never overwrite a foreign entry.
        stored = (read_lockfile_strict(path) or {}).get("packages", {}).get(slug)
        if _entry_is_exact(stored, expected, expected_hash):
            return                                       # our entry durably landed → committed
        if stored is not None:
            raise ToolpackCommitConflict() from exc      # a foreign entry is present
        raise ToolpackPublishFailed() from exc           # unrecorded → reparable

    # Mutation-strict confirmation that the EXACT expected entry landed (defence against a
    # replace slipping between the durable write and this read).
    stored = (read_lockfile_strict(path) or {}).get("packages", {}).get(slug)
    if _entry_is_exact(stored, expected, expected_hash):
        return
    if stored is not None:
        raise ToolpackCommitConflict()                   # replaced post-write — never overwrite
    raise ToolpackPublishFailed()                        # our entry not recorded → reparable


@contextmanager
def _host_env_write_lock(
    expected_identity,
    canonical_python: str,
    *,
    env: dict | None = None,
    timeout: float = ENV_WRITE_LOCK_TIMEOUT,
):
    """THE single authorizing production place for host-environment write-locking.

    Acquires exactly ONE environment write-lock (keyed by the resolved env identity),
    then RE-RESOLVES the identity under the lock and fails-closed on any change
    (:class:`EnvironmentIdentityChanged`) BEFORE yielding — an install bound to the old
    identity never mutates a different environment. Container / MCP / skill installs never
    call this (they perform no host-Python mutation).

    It yields the :class:`~agentnode_sdk._env_rwlock.EnvironmentWriteGuard` — the opaque,
    thread-bound proof the mutation helper verifies before touching the host.

    Every ACQUISITION error is translated to a uniform structured
    :class:`EnvironmentWriteLockError` (same contract for agent and toolpack): timeout,
    read->write upgrade, nested-write, and any lock-infrastructure failure (counter /
    ticket / queue-mutex / OS-lock). These are raised BEFORE the body runs → no mutation.

    LOCK ORDER (fixed, deadlock-free): this ``env_write_lock`` is the OUTER lock; the
    short per-lockfile ``file_lock`` inside ``mutate_lockfile`` / ``update_lockfile`` is
    the INNER lock. Never acquire them in the reverse order, and never wait on the
    env-write-lock while holding a file_lock."""
    from agentnode_sdk import _env_lock as envlock
    from agentnode_sdk import _env_rwlock as rw

    # --- Acquisition phase: translate EVERY lock error to a stable structured code. ---
    cm = rw.env_write_lock(expected_identity.env_id, timeout=timeout)
    try:
        guard = cm.__enter__()
    except rw.EnvironmentLockTimeout as e:
        raise EnvironmentWriteLockError(
            e.code, "Timed out acquiring the environment write-lock; no changes were made."
        ) from e
    except rw.ReadToWriteUpgradeForbidden as e:
        raise EnvironmentWriteLockError(
            e.code, "Cannot install while this process holds an environment reader."
        ) from e
    except rw.NestedEnvironmentWriteForbidden as e:
        raise EnvironmentWriteLockError(
            e.code, "This process already holds the environment write-lock."
        ) from e
    except (rw.CounterStateError, rw.CounterRollbackError, rw.QueueStateError,
            rw.TicketExhausted, rw.InternalStateError, OSError) as e:
        raise EnvironmentWriteLockError(
            ENVIRONMENT_WRITE_LOCK_FAILED,
            "The environment write-lock could not be established; no changes were made.",
        ) from e

    # --- Under the lock: identity recheck, then hand to the body; ALWAYS release. ---
    try:
        current = envlock.resolve_env_identity(canonical_python, env=env)
        if current != expected_identity:
            raise EnvironmentIdentityChanged()
        yield guard
    finally:
        cm.__exit__(None, None, None)   # release the ticket + local writer record


def _assert_json_safe(value: Any) -> None:
    """Recursively fail-closed (``PreparedInstallInvalid``) unless *value* is built only from
    JSON-safe types: None, bool, int, finite float, str, list, and dict with string keys.
    (``bool`` is a subclass of ``int`` — both are permitted.) NEVER coerces an unexpected
    object to a string; that is exactly the silent behaviour ``default=str`` would hide."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return
    if isinstance(value, list):
        for v in value:
            _assert_json_safe(v)
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise PreparedInstallInvalid("lock entry has a non-string mapping key")
            _assert_json_safe(v)
        return
    raise PreparedInstallInvalid(
        f"lock entry contains a non-JSON value of type {type(value).__name__}"
    )


def _canonical_lock_entry_bytes(entry: dict) -> bytes:
    """Validate the entry is JSON-safe (strict types) then serialize it canonically — sorted
    keys, compact separators, ASCII, no NaN/Inf, and crucially NO ``default=str``. The exact
    bytes are the immutable authorization stored in the prepared struct."""
    _assert_json_safe(entry)
    try:
        text = json.dumps(
            entry,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PreparedInstallInvalid("lock entry is not canonically serializable") from exc
    return text.encode("ascii")


@dataclass(frozen=True)
class _PreparedAgentInstall:
    """Immutable result of Phase A — every fact Phase B authorizes its mutation with, bound
    BEFORE the environment write-lock and never re-derived in a deeper helper.

    The authorization is stored as IMMUTABLE canonical JSON ``bytes`` (not a mutable dict):
    there is no second mutable snapshot window in which another thread or a stray reference
    could change the mapping between the digest check and the commit. ``commit_entry()``
    rebuilds a fresh object from those exact Phase-A bytes every time. ``controlled_env_items``
    is an immutable sorted key/value tuple (Phase B rebuilds a fresh env dict)."""
    slug: str
    lockfile_path: Path
    baseline_absent: bool
    baseline_hash: str | None
    top: str
    controlled_env_items: tuple       # immutable sorted (key, value) pairs
    canonical_python: str
    expected_env_identity: Any        # _env_lock.EnvIdentity (frozen)
    wheel: Path
    wheel_sha256: str
    distribution: str                 # wid.distribution (string)
    distribution_version: str         # wid.version (string)
    entrypoint: str                   # sealed entrypoint (string)
    artifact_hash: str
    publisher_slug: str | None
    version: str
    lock_entry_canonical_json: bytes  # IMMUTABLE canonical bytes — the sole authorization
    authorization_digest: str         # canonical digest over the bytes + bound primitives
    policy: str
    dest: Path                        # tempdir owning the built wheel (caller cleans it)

    def controlled_env(self) -> dict:
        """A FRESH mutable env dict rebuilt from the immutable stored pairs."""
        return dict(self.controlled_env_items)

    def lock_entry(self) -> dict:
        """A FRESH dict decoded from the immutable Phase-A bytes (read-only inspection)."""
        return json.loads(self.lock_entry_canonical_json)

    def commit_entry(self) -> dict:
        """A FRESH object rebuilt from the exact immutable Phase-A bytes — never a copy of a
        mutable in-memory snapshot, so the committed entry always equals the Phase-A bytes."""
        return json.loads(self.lock_entry_canonical_json)


def _prepared_authorization_digest(
    *,
    slug: str,
    version: str,
    artifact_hash: str,
    publisher_slug: str | None,
    distribution: str,
    distribution_version: str,
    entrypoint: str,
    wheel_sha256: str,
    env_id: str,
    top: str,
    lock_entry_canonical_json: bytes,
) -> str:
    """A canonical SHA-256 over every authorizing primitive PLUS the immutable entry bytes.
    Strict serialization (no ``default=str``); the primitives are all str/None."""
    header = json.dumps(
        {
            "slug": slug,
            "version": version,
            "artifact_hash": artifact_hash,
            "publisher_slug": publisher_slug,
            "distribution": distribution,
            "distribution_version": distribution_version,
            "entrypoint": entrypoint,
            "wheel_sha256": wheel_sha256,
            "env_id": env_id,
            "top": top,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(header + b"\0" + lock_entry_canonical_json).hexdigest()


def _recompute_prepared_digest(p: _PreparedAgentInstall) -> str:
    return _prepared_authorization_digest(
        slug=p.slug,
        version=p.version,
        artifact_hash=p.artifact_hash,
        publisher_slug=p.publisher_slug,
        distribution=p.distribution,
        distribution_version=p.distribution_version,
        entrypoint=p.entrypoint,
        wheel_sha256=p.wheel_sha256,
        env_id=p.expected_env_identity.env_id,
        top=p.top,
        lock_entry_canonical_json=p.lock_entry_canonical_json,
    )


def _verify_host_write_guard(guard: Any, env_id: str) -> None:
    """Fail-closed unless *guard* is the genuine, still-active write guard for *env_id*,
    held on the CURRENT thread. Rejects: no guard, wrong type, wrong environment, a
    different thread, an expired/released guard, and a self-constructed equal-looking token
    (the registry token is compared by identity, so a forgery cannot match)."""
    from agentnode_sdk import _env_rwlock as rw

    if not isinstance(guard, rw.EnvironmentWriteGuard):
        raise RuntimeError("internal: prepared install requires an environment write guard")
    if guard.env_id != env_id:
        raise RuntimeError("internal: write guard is for a different environment")
    if guard.owner_thread_id != threading.get_ident():
        raise RuntimeError("internal: write guard used off its owner thread")
    if not rw._writer_held_with(env_id, guard.token):
        raise RuntimeError("internal: write guard does not correspond to a HELD write-lock")


def _check_agent_cas_ownership(pkgs: dict, p: _PreparedAgentInstall, wm) -> Any:
    """Same-slug CAS + name-change + cross-slug ownership — RAISING, NO mutation. Used both
    read-only (preflight) and INSIDE the atomic quarantine mutator (race-closing re-check).
    Returns the current same-slug entry (or None)."""
    current = pkgs.get(p.slug)
    cur_hash = None
    if isinstance(current, dict):
        integ = current.get("_integrity")
        cur_hash = integ.get("hash") if isinstance(integ, dict) else None
    if p.baseline_absent:
        if current is not None:
            raise _CASConflict()
    elif current is None or cur_hash != p.baseline_hash:
        raise _CASConflict()
    if isinstance(current, dict):
        prev_dist = current.get("python_distribution")
        if prev_dist is not None and prev_dist != p.distribution:
            raise _NameChange()
    for oslug, oe in pkgs.items():
        if oslug == p.slug or not isinstance(oe, dict):
            continue
        if oe.get("package_type") != "agent":
            continue
        if p.distribution and oe.get("python_distribution") == p.distribution:
            raise _OwnershipConflict()
        oep = oe.get("entrypoint") or ""
        if oep:
            try:
                if wm.top_level_from_entrypoint(oep) == p.top:
                    raise _OwnershipConflict()
            except wm.WheelValidationError:
                pass
    return current


def _verify_prepared_bindings(p: _PreparedAgentInstall) -> None:
    """The authorizing entry (decoded fresh from the immutable Phase-A bytes) must still bind
    the same entry/artifact/publisher/version/entrypoint values as the prepared primitives (a
    second guard beyond the digest)."""
    e = p.lock_entry()
    if (
        e.get("version") != p.version
        or e.get("artifact_hash") != p.artifact_hash
        or (e.get("publisher_slug") or None) != (p.publisher_slug or None)
        or (e.get("entrypoint") or "") != p.entrypoint
    ):
        raise PreparedInstallStale("prepared authorization bindings no longer match")


def _prepare_agent_install_without_host_mutation(
    slug: str,
    *,
    lock_entry: dict,
    package_dir: Path,
    canonical_python: str,
    policy: str,
    prepared_baseline: tuple[bool, str | None] | None = None,
    lockfile_path: Path | None = None,
) -> _PreparedAgentInstall:
    """Phase A: build + validate the agent wheel and bind every authorizing fact into a
    deeply-immutable prepared struct WITHOUT mutating the host environment, WITHOUT holding
    the environment write-lock, and WITHOUT persisting the lockfile. The only lockfile touch
    is the short mutation-strict CAS-baseline READ, which is fully released before this
    returns — so the caller acquires the env write-lock with no sidecar lock held.

    ``prepared_baseline`` + ``lockfile_path`` (from the install-start snapshot) anchor the CAS
    and the lockfile path to the START of the operation; ``None`` → captured/resolved here
    (direct callers with no download phase)."""
    from agentnode_sdk import _agent_pip as ap
    from agentnode_sdk import _env_lock as envlock
    from agentnode_sdk import _wheel_meta as wm

    entrypoint = lock_entry.get("entrypoint") or ""
    lf = lockfile_path if lockfile_path is not None else _lockfile_path()

    # (1) CAS baseline anchored to the START of the operation (bound before download when
    # available; otherwise captured here, before the build).
    baseline_absent, baseline_hash = (
        prepared_baseline if prepared_baseline is not None
        else _capture_cas_baseline(slug, lf)
    )

    dest = Path(tempfile.mkdtemp(prefix="agentnode-wheel-"))
    try:
        # (2) entrypoint top-level (string-only; pre-build fail-closed).
        top = wm.top_level_from_entrypoint(entrypoint)
        # (3) ONE frozen controlled environment drives the config check, the build, BOTH
        #     pip steps, the target-interpreter probes and post-verify. Env identity +
        #     config-redirect refusal + install-scheme all run BEFORE the build, so a
        #     misconfigured / non-writable target refuses without running the backend.
        env = ap.controlled_pip_env()
        ident = envlock.resolve_env_identity(canonical_python, env=env)
        ap.assert_target_contract(canonical_python, env)
        ap.assert_install_scheme(canonical_python, env)
        # (4) build-wheel-first (controlled env) + (5) import-free validation.
        wheel = wm.build_wheel(canonical_python, package_dir, dest, env=env)
        wid = wm.validate_wheel(wheel, top)
        # (6) consistency digest captured before the pip hand-off.
        pre_hash = wm.wheel_consistency_hash(wheel)
    except BaseException:
        # Phase A failed → no prepared struct will exist to clean the tempdir; clean here.
        shutil.rmtree(dest, ignore_errors=True)
        raise

    # Freeze the authorizing entry into IMMUTABLE canonical bytes (strict JSON types,
    # fail-closed on anything else — no silent stringification). There is no mutable
    # snapshot after this, so the commit can only ever reproduce these exact Phase-A bytes.
    canonical_json = _canonical_lock_entry_bytes(lock_entry)
    frozen = json.loads(canonical_json)      # read authorizing primitives from the bytes
    digest = _prepared_authorization_digest(
        slug=slug,
        version=frozen.get("version") or "",
        artifact_hash=frozen.get("artifact_hash") or "",
        publisher_slug=frozen.get("publisher_slug"),
        distribution=wid.distribution,
        distribution_version=wid.version,
        entrypoint=entrypoint,
        wheel_sha256=pre_hash,
        env_id=ident.env_id,
        top=top,
        lock_entry_canonical_json=canonical_json,
    )
    return _PreparedAgentInstall(
        slug=slug,
        lockfile_path=lf,
        baseline_absent=baseline_absent,
        baseline_hash=baseline_hash,
        top=top,
        controlled_env_items=tuple(sorted(env.items())),
        canonical_python=canonical_python,
        expected_env_identity=ident,
        wheel=wheel,
        wheel_sha256=pre_hash,
        distribution=wid.distribution,
        distribution_version=wid.version,
        entrypoint=entrypoint,
        artifact_hash=frozen.get("artifact_hash") or "",
        publisher_slug=frozen.get("publisher_slug"),
        version=frozen.get("version") or "",
        lock_entry_canonical_json=canonical_json,
        authorization_digest=digest,
        policy=policy,
        dest=dest,
    )


def _install_agent_prepared_under_env_write_lock(
    prepared: _PreparedAgentInstall, guard: Any,
) -> None:
    """Phase B — the caller-owned host mutation. Acquires NO environment lock; it MUST run
    inside the caller's ``env_write_lock``, proven by the thread-bound *guard*. Strict order
    under that lock — NO lockfile mutation happens until every revalidation has passed:

      0. verify the write guard (type, env, owner thread, HELD state, exact token);
      1. prepared internal integrity (authorization digest unchanged);
      2/3. read-only classification of the current lockfile state (recovery of a prior
           aborted op is subsumed by the CAS-guarded atomic quarantine below — the durable-
           quarantine model leaves a valid CAS baseline either way, so no restore here);
      4. mutation-strict re-read;
      5. read-only same-slug CAS + name-change + cross-slug ownership preflight (RAISE, no
         mutation);
      6. prepared wheel present + regular file;
      7. recompute wheel digest and compare;
      8. entry/artifact/publisher/version/entrypoint binding checks;
      9. ONLY THEN apply the quarantine atomically — RE-CHECKING CAS + ownership INSIDE the
         mutator to close the preflight->mutation race;
      10. confirm absent → pip → post-verify → durable sealed commit (fresh deep copy).

    A tampered/deleted wheel or a mutated prepared struct fails-closed as
    :class:`PreparedInstallStale` BEFORE any quarantine — the prior entry stays byte-
    identical and pip never runs. Once the environment is mutating every failure is neutral
    (never restores an old entry)."""
    from agentnode_sdk import _agent_pip as ap
    from agentnode_sdk import _wheel_meta as wm
    from agentnode_sdk.lock_integrity import validate_python_distribution_fields

    p = prepared
    ident = p.expected_env_identity
    lf = p.lockfile_path

    # (0) GUARD — we MUST run under THIS write-lock, on the owner thread, HELD, exact token.
    _verify_host_write_guard(guard, ident.env_id)

    # (0b) POLICY — the host-trust policy bound at the operation's start must be unchanged
    # (no silent host↔container redirect mid-install). Under the lock, before any mutation.
    _assert_host_policy_unchanged(p.policy)

    # (1) Prepared internal integrity: the prepared struct must be unchanged since Phase A.
    if _recompute_prepared_digest(p) != p.authorization_digest:
        raise PreparedInstallStale("the prepared install was modified in memory")

    # (2/3/4) Read-only classify + mutation-strict re-read (recovery subsumed by CAS below).
    data = read_lockfile_strict(lf)
    pkgs = data.get("packages", {}) if isinstance(data, dict) else {}

    # (5) READ-ONLY preflight: CAS + name-change + ownership — RAISE, NO mutation.
    try:
        _check_agent_cas_ownership(pkgs, p, wm)
    except _CASConflict:
        raise _AgentTransactionAbort(
            "The agent was modified by another install; the stale build was not applied."
        )
    except _NameChange:
        raise _AgentTransactionAbort(
            "The agent's Python distribution name changed; automatic migration is not "
            "supported."
        )
    except _OwnershipConflict:
        raise _AgentTransactionAbort(
            "The installed Python distribution or import namespace is already owned by "
            "another agent."
        )

    # (6) wheel present + regular file, (7) digest unchanged, (8) bindings intact.
    if not p.wheel.is_file():
        raise PreparedInstallStale("the prepared wheel is missing")
    if wm.wheel_consistency_hash(p.wheel) != p.wheel_sha256:
        raise PreparedInstallStale("the prepared wheel changed on disk")
    _verify_prepared_bindings(p)

    # --- Every revalidation passed with NO mutation. Only now may we quarantine. ---

    # (9) ATOMIC quarantine — RE-CHECK CAS + ownership INSIDE the mutator (closes the
    #     read-only-preflight -> mutation race), then pop the slug.
    def _quarantine_apply(d: dict) -> bool:
        current = _check_agent_cas_ownership(d["packages"], p, wm)
        if current is None:
            return False  # new slug: already absent — nothing to write
        d["packages"].pop(p.slug)
        return True

    try:
        mutate_lockfile(_quarantine_apply, path=lf, audit_slug=p.slug, durable=True)
    except _CASConflict:
        raise _AgentTransactionAbort(
            "The agent was modified by another install; the stale build was not applied."
        )
    except _NameChange:
        raise _AgentTransactionAbort(
            "The agent's Python distribution name changed; automatic migration is not "
            "supported."
        )
    except _OwnershipConflict:
        raise _AgentTransactionAbort(
            "The installed Python distribution or import namespace is already owned by "
            "another agent."
        )
    except (_AgentTransactionAbort, RuntimeError):
        raise
    except Exception as exc:  # write/durability failure — don't start pip, no restore
        raise _AgentTransactionAbort(_AGENT_UNAVAILABLE_MSG) from exc

    # confirm the slug is absent (mutation-strict) after quarantine.
    confirm = read_lockfile_strict(lf)
    if confirm is not None and p.slug in confirm.get("packages", {}):
        raise _AgentTransactionAbort(_AGENT_UNAVAILABLE_MSG)

    # (10) From here the shared environment is mutated → every failure is neutral.
    env = p.controlled_env()
    try:
        ap.pip_install_wheel(p.canonical_python, p.wheel, env)
        ap.post_verify(
            p.canonical_python,
            expected_name=p.distribution,
            expected_version=p.distribution_version,
            expected_top_level=p.top,
            allowed_roots=[ident.purelib, ident.platlib],
            env=env,
        )
    except ap.AgentPipError as exc:
        raise RuntimeError(_AGENT_UNAVAILABLE_MSG) from exc

    # durable, sealed commit from a FRESH deep copy (the snapshot is never mutated).
    entry = p.commit_entry()
    entry["build_mode"] = "host"
    entry["effective_host_trust_policy_at_install"] = p.policy
    entry["pinnable"] = True
    entry["python_distribution"] = p.distribution
    entry["python_distribution_version"] = p.distribution_version
    validate_python_distribution_fields(entry)
    _commit_agent_entry(p.slug, entry, lf)


def _install_agent_host_transaction(
    slug: str,
    *,
    lock_entry: dict,
    package_dir: Path,
    target_python: str,
    policy: str,
    prepared_baseline: tuple[bool, str | None] | None = None,
    lockfile_path: Path | None = None,
) -> None:
    """The A1-M1 host-agent install — the agent HOST chokepoint. Phase A builds + binds the
    wheel with NO host mutation and NO lock; then exactly ONE environment write-lock is
    acquired (identity re-checked under it) and Phase B performs the caller-owned mutation.
    Self-committing on success; the old entry is never restored on failure.

    ``target_python`` is the already-resolved interpreter LAUNCH PATH chosen by the caller
    (venv-preserving; NOT physically canonicalised) — it is NOT re-resolved here (single
    resolution). ``prepared_baseline`` + ``lockfile_path`` are the
    same-slug CAS baseline and absolute lockfile path bound by ``install_package`` at the
    operation's START (anchoring the CAS + path); ``None`` → captured/resolved in Phase A
    (direct callers with no download phase, e.g. the M1 tests). ``policy`` is the bound
    host-trust policy, re-confirmed under the lock in Phase B."""
    from agentnode_sdk import _agent_pip as ap
    from agentnode_sdk import _env_lock as envlock
    from agentnode_sdk import _wheel_meta as wm

    prepared: _PreparedAgentInstall | None = None
    try:
        prepared = _prepare_agent_install_without_host_mutation(
            slug,
            lock_entry=lock_entry,
            package_dir=package_dir,
            canonical_python=target_python,
            policy=policy,
            prepared_baseline=prepared_baseline,
            lockfile_path=lockfile_path,
        )
        with _host_env_write_lock(
            prepared.expected_env_identity,
            prepared.canonical_python,
            env=prepared.controlled_env(),
        ) as guard:
            _install_agent_prepared_under_env_write_lock(prepared, guard)
    except (wm.WheelValidationError, ap.AgentPipError) as exc:
        # Only Phase-A (pre-mutation) build/metadata/target errors reach here and may be
        # specific; the mutating section (Phase B) already translates AgentPipError to the
        # neutral message, so a mutating AgentPipError never surfaces here.
        raise RuntimeError(exc.message) from exc
    except envlock.EnvironmentResolutionError as exc:
        raise RuntimeError("The target Python environment could not be resolved.") from exc
    except _AgentTransactionAbort as exc:
        raise RuntimeError(exc.message) from exc
    finally:
        if prepared is not None:
            shutil.rmtree(prepared.dest, ignore_errors=True)


def _install_toolpack_prepared_under_env_write_lock(
    slug: str,
    *,
    lock_entry: dict,
    package_dir: Path,
    canonical_python: str,
    policy: str,
    verbose: bool,
    expected_identity: Any,
    guard: Any,
    baseline_absent: bool,
    baseline_hash: str | None,
    lockfile_path: Path,
) -> None:
    """Host-toolpack mutation under the caller's env write-lock (guard-verified), with THREE
    distinct concurrency boundaries against the same-slug lockfile entry:

      1. PREPARATION-CAS (read-only): the current entry must STILL match the baseline bound at
         the START of the operation (BEFORE download) — else a newer install of the same
         package completed during our preparation → :class:`ToolpackPreparationStale` with NO
         mutation (the newer entry is left byte-identical).
      2. ATOMIC QUARANTINE-CAS: the SAME baseline is re-checked INSIDE the durable pop mutator
         (closes the preflight→quarantine race), then the old entry is removed — so after pip
         (possibly) changes the environment there is never an old, still-valid, executable
         entry. A brand-new slug (still absent) needs no quarantine write.
      3. FINAL COMPARE-AND-ADD (see ``_commit_toolpack_entry``): after pip, insert only while
         still absent — never overwrite an entry another path published during pip.

    Any failure AFTER the quarantine leaves the slug durably ABSENT (never restore the old
    entry); an explicit reinstall is required."""
    _verify_host_write_guard(guard, expected_identity.env_id)
    # The host-trust policy bound at the operation's start must be unchanged (no silent
    # host↔container redirect); under the lock, before any mutation.
    _assert_host_policy_unchanged(policy)
    lf = lockfile_path      # the BOUND absolute path — no re-resolution of _lockfile_path()

    # (1) PREPARATION-CAS (read-only, under the lock): still bound to the operation's start.
    data = read_lockfile_strict(lf)
    current = (data.get("packages", {}) if isinstance(data, dict) else {}).get(slug)
    if not _same_slug_matches_baseline(current, baseline_absent, baseline_hash):
        raise ToolpackPreparationStale()   # newer entry published during prep → no mutation

    # (2) DURABLE atomic pre-pip quarantine: re-check the SAME (pre-download) baseline INSIDE
    # the mutator (closes the preflight→quarantine race), then remove the old entry.
    def _quarantine(d: dict) -> bool:
        cur = d["packages"].get(slug)
        if not _same_slug_matches_baseline(cur, baseline_absent, baseline_hash):
            raise _CASConflict()
        if cur is None:
            return False
        d["packages"].pop(slug)
        return True

    try:
        mutate_lockfile(_quarantine, path=lf, audit_slug=slug, durable=True)
    except _CASConflict:
        raise _AgentTransactionAbort(
            "The toolpack was modified by another install; the install was not applied."
        )
    except (_AgentTransactionAbort, RuntimeError):
        raise
    except Exception as exc:  # write/durability failure — old entry not removed, no pip
        raise _AgentTransactionAbort(_TOOLPACK_UNAVAILABLE_MSG) from exc

    # Confirm the old entry is durably gone before touching the environment.
    confirm = read_lockfile_strict(lf)
    if confirm is not None and slug in confirm.get("packages", {}):
        raise _AgentTransactionAbort(_TOOLPACK_UNAVAILABLE_MSG)

    # From here pip may mutate the environment. Any failure leaves the slug ABSENT (the old
    # entry has already been durably quarantined — it can never run against the changed env).
    pip_install(canonical_python, package_dir, verbose=verbose)   # raises on failure → absent

    # Publish the NEW entry as an ATOMIC compare-and-ADD (NOT a plain overwrite): while pip
    # ran, another path (container / different target env / any non-serialized route) may
    # have published this slug. The final commit inserts our entry ONLY if the slug is still
    # absent; a concurrent entry → ToolpackCommitConflict (never overwritten). A durability
    # failure with our entry unrecorded → the reparable ToolpackPublishFailed. Both leave the
    # old entry (already durably quarantined) absent — never restored.
    lock_entry["build_mode"] = "host"
    lock_entry["effective_host_trust_policy_at_install"] = policy
    lock_entry["pinnable"] = True
    _commit_toolpack_entry(slug, lock_entry, lf)


def _install_toolpack_host_transaction(
    slug: str,
    *,
    lock_entry: dict,
    package_dir: Path,
    target_python: str,
    policy: str,
    verbose: bool,
    prepared_baseline: tuple[bool, str | None] | None = None,
    lockfile_path: Path | None = None,
) -> None:
    """The host-toolpack HOST chokepoint: resolve the target env identity, acquire exactly
    ONE environment write-lock (identity re-checked under it), and run the durable-quarantine
    toolpack transaction. ``target_python`` is the already-resolved interpreter launch path
    (venv-preserving; single resolution — NOT physically canonicalised).

    ``prepared_baseline`` + ``lockfile_path`` are the same-slug CAS baseline and absolute
    lockfile path bound by ``install_package`` at the operation's START (anchor the
    preparation-CAS + path). ``None`` → captured/resolved here (direct callers with no
    download phase). ``policy`` is re-confirmed under the lock before any mutation."""
    from agentnode_sdk._env_lock import resolve_env_identity

    lf = lockfile_path if lockfile_path is not None else _lockfile_path()
    expected_identity = resolve_env_identity(target_python)
    baseline_absent, baseline_hash = (
        prepared_baseline if prepared_baseline is not None
        else _capture_cas_baseline(slug, lf)
    )
    try:
        with _host_env_write_lock(expected_identity, target_python) as guard:
            _install_toolpack_prepared_under_env_write_lock(
                slug,
                lock_entry=lock_entry,
                package_dir=package_dir,
                canonical_python=target_python,
                policy=policy,
                verbose=verbose,
                expected_identity=expected_identity,
                guard=guard,
                baseline_absent=baseline_absent,
                baseline_hash=baseline_hash,
                lockfile_path=lf,
            )
    except _AgentTransactionAbort as exc:
        raise RuntimeError(exc.message) from exc


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
    # A1-E-Lock L3: OPTIONAL explicit target interpreter for the HOST-mutation routes
    # (host-agent / host-toolpack). None → the canonical ``sys.executable``. Public callers
    # without an interpreter choice pass None; the internal / direct installer path passes
    # a specific interpreter so two venvs can be addressed and the environment write-lock
    # is genuinely keyed by the TARGET, not by the calling process. Ignored on
    # container / MCP / skill routes (they perform no host-Python mutation).
    target_python: str | Path | None = None,
    # A1-E-Lock L3: the install-start snapshot bound by the caller (the client) at the
    # EARLIEST start of the operation — before registry resolution / version selection. It
    # anchors the same-slug baseline, the lockfile path, and the host-trust policy. Direct /
    # legacy callers pass None → captured at entry (still before download).
    start_snapshot: _InstallStartSnapshot | None = None,
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

    # A1-E-Lock L3 — bind (or validate) the install-start SNAPSHOT. Its same-slug baseline,
    # absolute lockfile path, and host-trust policy were captured at the EARLIEST start of the
    # operation (before registry resolution / version selection, and before the download/build
    # below). All host-mutation phases use THESE bound values — never a fresh read of the
    # process-global lockfile path / policy — so a concurrently-completed newer install cannot
    # be adopted as this operation's baseline and path/policy cannot drift mid-transaction.
    snapshot = _bind_or_capture_install_snapshot(start_snapshot, slug)
    bound_lockfile = snapshot.lockfile_path
    bound_policy = snapshot.host_policy_snapshot
    host_baseline: tuple[bool, str | None] = (
        snapshot.baseline_absent, snapshot.baseline_integrity_hash)

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

        # NOTE (A1-E-Lock L3): the target interpreter is resolved ONLY on the actual
        # host-mutation route (below), never here — a container/MCP/skill install must
        # not fail-close on an unusable sys.executable when it never touches host Python.

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

        # Step 7: build — only reached after every gate (hash + signature) passed. The route
        # (host vs container) is decided from the BOUND snapshot policy, not a fresh read — an
        # operation is never silently redirected host↔container by a mid-flight policy change.
        from agentnode_sdk.sandbox.policy import host_allowed_tiers

        if (trust_level or "").lower() in host_allowed_tiers(bound_policy):
            # HOST mutation route ONLY: resolve the target interpreter LAUNCH PATH here
            # (venv-preserving; single resolution; container / MCP / skill never reach this).
            # Agent AND toolpack each run a durable-quarantine transaction under exactly ONE
            # env write-lock, so a failure after pip never leaves an old executable entry. The
            # BOUND lockfile path + policy are threaded through (no re-resolution); the policy
            # is re-confirmed under the lock before the first mutation.
            launch_python = resolve_python(target_python)
            if package_type == "agent":
                _install_agent_host_transaction(
                    slug,
                    lock_entry=lock_entry,
                    package_dir=package_dir,
                    target_python=launch_python,
                    policy=bound_policy,
                    prepared_baseline=host_baseline,
                    lockfile_path=bound_lockfile,
                )
            else:
                _install_toolpack_host_transaction(
                    slug,
                    lock_entry=lock_entry,
                    package_dir=package_dir,
                    target_python=launch_python,
                    policy=bound_policy,
                    verbose=verbose,
                    prepared_baseline=host_baseline,
                    lockfile_path=bound_lockfile,
                )
        else:
            sandbox_volume = _container_build_into_volume(
                slug, version, package_dir, f"sha256:{local_hash}",
            )
            lock_entry["sandboxed"] = True
            lock_entry["sandbox_volume"] = sandbox_volume
            lock_entry["build_mode"] = "sandbox_volume"
            # Container build performs NO host-Python mutation → no environment write-lock;
            # publish under the BOUND lockfile path with the BOUND policy metadata. MUTABLE
            # metadata (NOT sealed) lets the doctor tell a host build from a sandboxed one;
            # containers build from the pinned artifact, so they are pinnable.
            lock_entry["effective_host_trust_policy_at_install"] = bound_policy
            lock_entry["pinnable"] = True
            from agentnode_sdk.lock_integrity import seal_entry
            update_lockfile(slug, seal_entry(lock_entry), path=bound_lockfile)

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
