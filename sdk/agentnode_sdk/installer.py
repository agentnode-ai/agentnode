"""Local package installation for AgentNode SDK.

Ports the CLI install flow (§13.4) to Python so agents can
install capabilities programmatically without human intervention.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
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
    """Find a usable Python 3 interpreter.

    Resolution order:
    1. $VIRTUAL_ENV/bin/python (or Scripts/python.exe on Windows)
    2. .venv/bin/python in cwd
    3. python3 on PATH
    4. python on PATH
    """
    is_windows = sys.platform == "win32"

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

def _verify_publisher_signature(slug: str, lock_entry: dict) -> None:
    """Verify publisher signature on a lock entry before writing.

    - Valid → silent
    - Missing → warn (install continues)
    - Invalid / malformed → raise RuntimeError (install blocked)
    """
    import logging
    import warnings

    from agentnode_sdk.signature import verify_entry_signature, SignatureStatus

    result = verify_entry_signature(slug, lock_entry)

    if result.status == SignatureStatus.VALID:
        logging.getLogger(__name__).info(
            "Publisher signature valid for %s (key %s)", slug, result.key_id,
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
        )

    tmpdir = Path(tempfile.mkdtemp(prefix="agentnode-"))
    try:
        tar_path = tmpdir / "package.tar.gz"
        extract_dir = tmpdir / "extracted"
        extract_dir.mkdir()

        # Step 2: Download
        download_artifact(artifact_url, tar_path)

        # Step 3: Verify hash
        local_hash = verify_hash(tar_path, artifact_hash)

        # Step 4: Extract & validate
        package_dir = extract_archive(tar_path, extract_dir)

        # Step 5: Verify entrypoint (non-fatal)
        _verify_entrypoint(package_dir, entrypoint)

        # Step 6: Resolve Python
        python = resolve_python()

        # Step 7: pip install
        pip_install(python, package_dir, verbose=verbose)

        # Step 8: Update lockfile
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
        if remote_endpoint:
            lock_entry["remote_endpoint"] = remote_endpoint

        if signatures:
            if publisher_slug:
                for sig in (signatures.get("publisher") or []):
                    if isinstance(sig, dict):
                        sig["publisher_slug"] = publisher_slug
            lock_entry["_signatures"] = signatures
        _verify_publisher_signature(slug, lock_entry)

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
        _verify_publisher_signature(slug, lock_entry)

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

    # v0.1 fallback / single-tool: use package-level entrypoint
    entrypoint = pkg.get("entrypoint")
    if not entrypoint:
        raise ImportError(f"Package '{slug}' has no entrypoint in lockfile.")

    module_path, func_name = _resolve_entrypoint(entrypoint)
    mod = _import_module(module_path, slug)
    func = getattr(mod, func_name, None)
    if func is None:
        raise ImportError(
            f"Function '{func_name}' not found in module '{module_path}' "
            f"for package '{slug}'."
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
