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

# Stage 4A: deterministic tree-hash of the built /install volume, computed INSIDE the
# container (no host tool, no secrets). Robust + UNAMBIGUOUS: each entry is a JSON
# object (so weird filenames with tabs/newlines are escaped) hashed length-prefixed,
# binding relative path + type (f/d/l) + octal mode (executable bit) + symlink target +
# regular-file content sha256. mtime/owner are excluded (non-deterministic). Entries are
# globally sorted by path → order-independent. Fail-closed: any error exits non-zero
# (the outer `set -e` propagates → caller tears down the volume and raises). This is the
# value sealed as ``mcp_preinstall.artifact_hash`` (Stage 4B verifies content↔hash).
_MCP_HASH_PY = r'''
import os, stat, json, hashlib
root = "/install"
entries = []
for dirpath, dirnames, filenames in os.walk(root):
    for name in dirnames + filenames:
        p = os.path.join(dirpath, name)
        rel = os.path.relpath(p, root)
        mode = os.lstat(p).st_mode
        perm = oct(stat.S_IMODE(mode))
        if stat.S_ISLNK(mode):
            rec = {"p": rel, "t": "l", "m": perm, "link": os.readlink(p)}
        elif stat.S_ISDIR(mode):
            rec = {"p": rel, "t": "d", "m": perm}
        elif stat.S_ISREG(mode):
            fh = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    fh.update(chunk)
            rec = {"p": rel, "t": "f", "m": perm, "sha": fh.hexdigest()}
        else:
            rec = {"p": rel, "t": "o", "m": perm}
        entries.append(rec)
entries.sort(key=lambda r: r["p"])
h = hashlib.sha256()
for rec in entries:
    line = json.dumps(rec, sort_keys=True, separators=(",", ":")).encode("utf-8")
    h.update(len(line).to_bytes(8, "big"))
    h.update(line)
print("HASH:" + h.hexdigest())
bindir = os.path.join(root, "bin")
bins = sorted(os.listdir(bindir)) if os.path.isdir(bindir) else []
print("BINS:" + ",".join(bins))
'''


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


def read_lockfile(path: Path | None = None) -> dict:
    lf = path or _lockfile_path()
    if lf.is_file():
        try:
            data = json.loads(lf.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "packages" in data:
                return data
        except (json.JSONDecodeError, OSError):
            import logging
            logging.getLogger("agentnode.installer").warning(
                "Lockfile corrupted or unreadable: %s — treating as empty", lf
            )
    return {"lockfile_version": LOCKFILE_VERSION, "updated_at": "", "packages": {}}


def update_lockfile(
    slug: str,
    entry: dict[str, Any],
    path: Path | None = None,
) -> None:
    """Write or update a package entry in agentnode.lock."""
    from agentnode_sdk._fileutil import atomic_write_json, file_lock

    lf = path or _lockfile_path()
    with file_lock(lf):
        data = read_lockfile(lf)
        data["packages"][slug] = entry
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(lf, data)


def check_installed(slug: str, version: str, path: Path | None = None) -> str:
    """Check lockfile status. Returns 'same', 'different', or 'missing'."""
    data = read_lockfile(path)
    pkg = data.get("packages", {}).get(slug)
    if not pkg:
        return "missing"
    return "same" if pkg.get("version") == version else "different"


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

# P0.0: tiers permitted to run build hooks (setup.py / PEP-517) natively on the
# host. Non-trusted (community) packages must NOT build on the host — a sandboxed
# build path is pending (P0.3). Artifacts are source trees (no wheel), so this is
# a fail-closed gate, not a wheel fallback.
_HOST_BUILD_TIERS = {"trusted", "curated"}


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
    # TG-2: registry-reported key status (install-time revocation)
    key_status: str | None = None,
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
        # curated/trusted build natively on the host; every other tier is built
        # INSIDE the container into a deterministic volume (fail-closed if no
        # runtime — never a host build for community code).
        if (trust_level or "").lower() in _HOST_BUILD_TIERS:
            pip_install(python, package_dir, verbose=verbose)
        else:
            sandbox_volume = _container_build_into_volume(
                slug, version, package_dir, f"sha256:{local_hash}",
            )
            lock_entry["sandboxed"] = True
            lock_entry["sandbox_volume"] = sandbox_volume

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
    into Stage 4A pre-install into a sealed volume)."""
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
        lock_entry["mcp_preinstalled"] = True
        lock_entry["mcp_preinstall"] = {
            "manager": mgr,
            "package": pkg,
            "version": pkg_ver,
            "artifact_hash": tree_hash,
        }
        lock_entry["mcp_sandbox_volume"] = volume
        lock_entry["mcp_preinstall_command"] = preinstall_command

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


def load_tool(slug: str, tool_name: str | None = None, *, _internal: bool = False) -> Any:
    """Load an installed package's tool function.

    Args:
        slug: Package slug (e.g. "csv-analyzer-pack").
        tool_name: Optional tool name for multi-tool v0.2 packs.
            If None, uses the package-level entrypoint (v0.1 behavior).

    Returns the callable tool function.
    For v0.1 packs: returns module.run
    For v0.2 packs with tool_name: returns the specific tool function
    """
    if not _internal:
        import warnings
        warnings.warn(
            "load_tool() bypasses policy checks. Use run_tool() for safe execution.",
            RuntimeWarning,
            stacklevel=2,
        )
    data = read_lockfile()
    pkg = data.get("packages", {}).get(slug)
    if not pkg:
        raise ImportError(
            f"Package '{slug}' is not installed. "
            f"Install it first: client.install('{slug}')"
        )

    # v0.2: check for per-tool entrypoints in lockfile
    if tool_name:
        tools = pkg.get("tools", [])
        for t in tools:
            if t.get("name") == tool_name:
                ep = t.get("entrypoint", "")
                if ep:
                    module_path, func_name = _resolve_entrypoint(ep)
                    mod = _import_module(module_path, slug)
                    func = getattr(mod, func_name, None)
                    if func is None:
                        raise ImportError(
                            f"Function '{func_name}' not found in module '{module_path}' "
                            f"for tool '{tool_name}' in package '{slug}'."
                        )
                    return func
        # Fallback: tool_name given but not in tools list — use package-level entrypoint
        # Most tool-packs have a single run() function that handles all operations.
        # The tool_name is passed by the caller but maps to the same entrypoint.
        # Only attempt fallback if no explicit tools list exists (v0.1 pack).
        entrypoint = pkg.get("entrypoint")
        if entrypoint and not tools:
            module_path, func_name = _resolve_entrypoint(entrypoint)
            mod = _import_module(module_path, slug)
            # Try tool_name as function name in the module first
            func = getattr(mod, tool_name, None)
            if func and callable(func):
                return func
            # Fall back to the default entrypoint function (usually run())
            func = getattr(mod, func_name, None)
            if func and callable(func):
                return func

        raise ImportError(
            f"Tool '{tool_name}' not found in package '{slug}'. "
            f"Available tools: {[t.get('name') for t in pkg.get('tools', [])]}"
        )

    # No tool_name: auto-select the sole tool when exactly one is declared,
    # else fall back to the package-level entrypoint (multi-tool unchanged).
    entrypoint = _default_tool_entrypoint(pkg)
    if not entrypoint:
        raise ImportError(
            f"Package '{slug}' has no entrypoint in lockfile."
            + _multi_tool_hint(pkg, slug)
        )

    module_path, func_name = _resolve_entrypoint(entrypoint)
    mod = _import_module(module_path, slug)
    func = getattr(mod, func_name, None)
    if func is None:
        raise ImportError(
            f"Function '{func_name}' not found in module '{module_path}' "
            f"for package '{slug}'."
            + _multi_tool_hint(pkg, slug)
        )
    return func


def _import_module(module_path: str, slug: str) -> Any:
    """Import a Python module by dotted path."""
    try:
        return importlib.import_module(module_path)
    except ImportError:
        raise ImportError(
            f"Could not import '{module_path}' for package '{slug}'. "
            "The package may need to be reinstalled."
        )
