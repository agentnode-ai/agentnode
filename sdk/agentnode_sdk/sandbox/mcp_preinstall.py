"""Shared MCP pre-install validation + content-hash verification.

The deterministic tree-hash logic lives here ONCE (`_MCP_HASH_PY`): Stage 4A's
install-time build (``installer._container_build_mcp_volume``) and Stage 4B's run-time
verifier both import this exact constant, so the build hash and the verify hash can never
drift. Stage 4B run-path consumers also use the pure validators here.

Everything here is fail-closed: a preinstalled MCP that does not validate, or whose volume
content does not match the sealed ``mcp_preinstall.artifact_hash``, is REFUSED — never a
fallback to the registry-fetch (``npx``/``uvx``) ``mcp_command`` path, never a key, never an
auto-created/rebuilt volume.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

# The ONE definition of the tree-hash program (moved from installer.py, Stage 4A). It is
# run inside the container — at build time (4A) and at verify time (4B) — over /install:
# per entry a length-prefixed JSON record binding relative path + type (f/d/l) + octal
# mode + symlink target + file-content sha256; globally sorted; final sha256. mtime/owner
# excluded (non-deterministic). Prints `HASH:<hex>` and `BINS:<comma-list>`.
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

_ARTIFACT_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ALLOWED_INTERP = ("node", "python")
_SHELL_META = set(";|&`$<>") | {" ", "\t", "~"}


class PreinstallError(RuntimeError):
    """A preinstalled MCP cannot be run safely (fail-closed). Carries no secret. The
    caller must NOT fall back to the registry-fetch mcp_command path on this error."""


@dataclass(frozen=True)
class PreinstallSpec:
    manager: str            # "npm" | "pypi"
    package: str            # manager package name
    package_version: str    # manager package version (NOT the MCP/ANP version)
    artifact_hash: str      # sha256:<64 lowercase hex>
    volume: str             # the sealed, descriptor-bound volume name
    command: tuple[str, ...]  # validated [node|python, /install/...]


def has_preinstall_intent(entry) -> bool:
    """True if the lockfile entry intends the preinstalled path. Once this is true the
    run path is committed to fail-closed preinstall handling — NEVER the mcp_command path."""
    if not isinstance(entry, dict):
        return False
    if entry.get("mcp_preinstalled") is True:
        return True
    return any(
        k in entry for k in ("mcp_preinstall", "mcp_sandbox_volume", "mcp_preinstall_command")
    )


def validate_preinstall_command(cmd) -> list[str]:
    """Strict: exactly ``[node|python, /install/<path>]``. No shell string, no sh/bash/
    cmd/powershell/-c, no npx/uvx/npm/pip; the path must be a normalized absolute path
    under ``/install/`` with NO empty/`.`/`..` segment, no backslash, no NUL/CR/LF, no
    shell metachar. Fail-closed (PreinstallError)."""
    if not isinstance(cmd, list) or len(cmd) != 2 or not all(isinstance(x, str) for x in cmd):
        raise PreinstallError("mcp_preinstall_command must be a 2-element list of strings")
    interp, path = cmd
    if interp not in _ALLOWED_INTERP:
        raise PreinstallError(
            f"mcp_preinstall_command interpreter must be node or python, got {interp!r}"
        )
    if not path.startswith("/install/"):
        raise PreinstallError(
            f"mcp_preinstall_command path must be an absolute path under /install/, got {path!r}"
        )
    # No backslash / NUL / CR / LF / shell metachar anywhere.
    if any(c in path for c in ("\\", "\0", "\n", "\r")) or any(c in path for c in _SHELL_META):
        raise PreinstallError(f"mcp_preinstall_command path is unsafe: {path!r}")
    # Reject empty / "." / ".." segments (parts[2:] skips the leading "" + "install").
    parts = path.split("/")
    if any(seg in ("", ".", "..") for seg in parts[2:]):
        raise PreinstallError(f"mcp_preinstall_command path has an unsafe segment: {path!r}")
    # Must already be canonical and still under /install/ (no normalization tricks).
    if posixpath.normpath(path) != path or not path.startswith("/install/"):
        raise PreinstallError(f"mcp_preinstall_command path is not normalized: {path!r}")
    return [interp, path]


def validate_preinstall_fields(mcp_slug: str, mcp_version, entry) -> PreinstallSpec:
    """Pure, no side effects, no container. Validate the sealed Stage-4A fields and return
    a PreinstallSpec, or raise PreinstallError. ``mcp_version`` is the MCP/ANP package
    version; ``mcp_preinstall['version']`` is the manager package_version — they are NOT
    interchangeable."""
    from agentnode_sdk.sandbox.container_backend import mcp_sandbox_volume_name

    if entry.get("mcp_preinstalled") is not True:
        raise PreinstallError("mcp_preinstalled is not True")
    pre = entry.get("mcp_preinstall")
    if not isinstance(pre, dict) or set(pre) != {"manager", "package", "version", "artifact_hash"}:
        raise PreinstallError(
            "mcp_preinstall must be an object with exactly manager/package/version/artifact_hash"
        )
    manager = pre["manager"]
    package = pre["package"]
    package_version = pre["version"]
    artifact_hash = pre["artifact_hash"]
    if manager not in ("npm", "pypi"):
        raise PreinstallError(f"mcp_preinstall.manager must be 'npm' or 'pypi', got {manager!r}")
    if not isinstance(package, str) or not package:
        raise PreinstallError("mcp_preinstall.package must be a non-empty string")
    if not isinstance(package_version, str) or not package_version:
        raise PreinstallError("mcp_preinstall.version (package_version) must be a non-empty string")
    if not isinstance(artifact_hash, str) or not _ARTIFACT_HASH_RE.match(artifact_hash):
        raise PreinstallError("mcp_preinstall.artifact_hash must be sha256:<64 lowercase hex>")
    expected_vol = mcp_sandbox_volume_name(mcp_slug, mcp_version, manager, package, package_version)
    vol = entry.get("mcp_sandbox_volume")
    if not isinstance(vol, str) or vol != expected_vol:
        raise PreinstallError("mcp_sandbox_volume does not match the recomputed descriptor name")
    command = validate_preinstall_command(entry.get("mcp_preinstall_command"))
    return PreinstallSpec(manager, package, package_version, artifact_hash, vol, tuple(command))


def verify_volume_content(backend, volume: str, expected_hash: str) -> None:
    """Run the SHARED ``_MCP_HASH_PY`` in a throwaway container that mounts ``volume`` RO at
    /install (network=none, env={}, clean HOME, no host mounts, no secrets) and raise
    PreinstallError unless the computed content hash exactly equals ``expected_hash``.
    Caller must have already confirmed the volume EXISTS (``volume inspect``) so this does
    not silently auto-create one."""
    from agentnode_sdk.sandbox.types import MountSpec

    spec = backend.build_process_spec(
        ["python3", "-c", _MCP_HASH_PY],
        network="none",
        mounts=[MountSpec(src=volume, dst="/install", read_only=True)],
        env={},
        clean_home=True,
    )
    rc, stdout, _stderr = backend.run_process(spec, timeout=60)
    if rc != 0:
        raise PreinstallError(
            f"volume content verification failed (exit {rc}) — reinstall required"
        )
    got = ""
    for line in (stdout or "").splitlines():
        if line.startswith("HASH:"):
            got = "sha256:" + line[len("HASH:"):].strip()
            break
    if got != expected_hash:
        raise PreinstallError(
            "volume content hash does not match the sealed artifact_hash — reinstall required"
        )
