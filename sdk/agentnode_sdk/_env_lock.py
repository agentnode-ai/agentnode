"""Target-environment identity + environment-scoped install lock (Agent-Exec A1-M1).

The install lock must protect the SHARED Python environment, not a slug and not a
single lockfile. ``_lockfile_path()`` is CWD-relative (``AGENTNODE_LOCKFILE``-
overridable), so several lockfiles can drive installs into the *same*
``resolve_python()`` interpreter. A per-slug or per-lockfile lock therefore does not
serialize those installs. The lock here is keyed by the canonical target
interpreter + its purelib/platlib install roots, so every install into one shared
site-packages is serialized while genuinely separate environments run in parallel.

Honest boundary (documented, enforced elsewhere): this lock serializes INSTALLERS,
not agent EXECUTIONS. It does not provide cross-lockfile ownership or an
environment-wide runtime quarantine — those are A1-Enforcement scope.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from agentnode_sdk._fileutil import file_lock

# Import-free interrogation of the TARGET interpreter. Imports only stdlib
# (sys/sysconfig/site/os/json) — never agent code, never a third-party module.
_PROBE = (
    "import sys,sysconfig,site,os,json;"
    "d={"
    "'executable':sys.executable,"
    "'prefix':sys.prefix,"
    "'purelib':sysconfig.get_path('purelib'),"
    "'platlib':sysconfig.get_path('platlib'),"
    "'user_site':(site.getusersitepackages() if hasattr(site,'getusersitepackages') else None),"
    "'version':sys.version.split()[0],"
    "};"
    "print(json.dumps(d))"
)

_PROBE_TIMEOUT = 30


class EnvironmentResolutionError(Exception):
    """The target interpreter could not be interrogated import-free."""


@dataclass(frozen=True)
class EnvIdentity:
    """Canonical identity of the target install environment.

    ``env_id`` binds the interpreter and BOTH install roots (purelib + platlib), so
    two lockfiles resolving to the same interpreter map to the same lock, and a
    redirected platlib (C-extension root) is not silently missed.
    """
    target_python: str        # canonical realpath of the interrogated executable
    sys_prefix: str
    purelib: str              # canonical realpath
    platlib: str              # canonical realpath
    user_site: str | None     # canonical realpath (verification only — never a target)
    version: str
    env_id: str               # sha256(target_python \0 purelib \0 platlib)


def _canon(path: str | None) -> str | None:
    if not path:
        return None
    return os.path.normcase(os.path.realpath(path))


def resolve_env_identity(target_python: str, *, env: dict | None = None) -> EnvIdentity:
    """Interrogate *target_python* import-free and return its canonical identity.

    ``env`` is the SINGLE frozen controlled environment (same snapshot used for the
    config check, build, install and post-verify). ``None`` inherits.
    """
    try:
        proc = subprocess.run(
            [target_python, "-c", _PROBE],
            capture_output=True, timeout=_PROBE_TIMEOUT, env=env,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise EnvironmentResolutionError("target interpreter probe failed") from exc
    if proc.returncode != 0:
        raise EnvironmentResolutionError("target interpreter probe failed")
    try:
        data = json.loads(proc.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise EnvironmentResolutionError("target interpreter probe returned no identity") from exc

    executable = _canon(data.get("executable")) or _canon(target_python)
    purelib = _canon(data.get("purelib"))
    platlib = _canon(data.get("platlib"))
    if not executable or not purelib or not platlib:
        raise EnvironmentResolutionError("target interpreter has no usable install roots")

    payload = f"{executable}\0{purelib}\0{platlib}".encode("utf-8")
    env_id = hashlib.sha256(payload).hexdigest()
    return EnvIdentity(
        target_python=executable,
        sys_prefix=data.get("prefix") or "",
        purelib=purelib,
        platlib=platlib,
        user_site=_canon(data.get("user_site")),
        version=data.get("version") or "",
        env_id=env_id,
    )


def _env_lock_dir() -> Path:
    # config_dir() honours AGENTNODE_CONFIG, so tests isolate the lock directory.
    from agentnode_sdk.config import config_dir
    return config_dir() / "locks"


@contextmanager
def env_lock(env_id: str):
    """Hold the environment-scoped inter-process install lock for *env_id*.

    Reuses the proven cross-platform :func:`file_lock` primitive (advisory,
    released on process crash). The lock file lives in a stable global AgentNode
    directory and is named ``env-<env_id>.lock`` — a hash, never a raw path.

    LOCK ORDER (fixed, deadlock-free): this environment lock is the OUTER lock. The
    short-lived per-lockfile ``file_lock`` inside ``mutate_lockfile`` is the INNER
    lock. Never acquire them in the reverse order, and never hold the per-lockfile
    lock across a pip call.
    """
    lock_dir = _env_lock_dir()
    lock_dir.mkdir(parents=True, exist_ok=True)
    with file_lock(lock_dir / f"env-{env_id}.lock"):
        yield
