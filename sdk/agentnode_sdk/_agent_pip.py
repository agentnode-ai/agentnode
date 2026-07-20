"""Controlled agent pip target + import-free post-verify (Agent-Exec A1-M1).

The legacy ``installer.pip_install`` inherits the whole environment unchecked, so
``PIP_TARGET`` / ``PIP_PREFIX`` / ``PIP_USER`` / a pip config ``target=`` can send an
agent install to a root the environment-scoped lock never covers. This module gives
the host-agent path a controlled target:

* env-level redirects are neutralized (delete ``PIP_TARGET`` / ``PIP_PREFIX`` /
  ``PIP_ROOT`` / ``PIP_USER`` / ``PYTHONUSERBASE``; set ``PYTHONNOUSERSITE=1`` and
  ``PIP_USER=0``) — index / proxy / credential config is preserved;
* config-level redirects (``*.target`` / ``*.prefix`` / ``*.root``) — which
  EMPIRICALLY win over an absent/empty env var — are DETECTED and refused before any
  environment mutation (`assert_target_contract`);
* the two-step install (`pip install <wheel>` then `--force-reinstall --no-deps
  <wheel>`) guarantees the exact wheel bytes while keeping today's dependency
  semantics;
* `post_verify` re-checks, import-free and in the target interpreter, that the
  distribution landed ONLY under purelib/platlib as the sole provider of the
  entrypoint top-level.

Empirically grounded (see the A1-M1 PR report): `PIP_TARGET=""` does NOT override a
config ``target=`` (config wins), but `PIP_USER=0` DOES override config ``user=true``;
hence config ``target``/``prefix``/``root`` need a pre-flight refusal while ``user`` is
handled by the env override plus post-verify.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version

_PIP_TIMEOUT = 120
_CONFIG_TIMEOUT = 30

# env vars that redirect the install target and CAN be neutralized by removal.
_REDIRECT_ENV = ("PIP_TARGET", "PIP_PREFIX", "PIP_ROOT", "PIP_USER", "PYTHONUSERBASE")
# config keys (suffix after the section, e.g. ``global.target``) that redirect the
# root and CANNOT be overridden by an absent/empty env var → refuse pre-quarantine.
_REDIRECT_CONFIG_SUFFIXES = (".target", ".prefix", ".root")


class AgentPipError(Exception):
    """A controlled pip step failed. ``code`` is a content-free token; ``message``
    is the safe operator string (no absolute paths / pip dumps — §19)."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def controlled_pip_env() -> dict[str, str]:
    """Return an environment for pip that neutralizes env-level target redirects
    while preserving index / proxy / credential configuration."""
    env = dict(os.environ)
    for var in _REDIRECT_ENV:
        env.pop(var, None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PIP_USER"] = "0"
    return env


def assert_target_contract(target_python: str, env: dict[str, str]) -> None:
    """Refuse (pre-quarantine) if a pip CONFIG redirect we cannot neutralize is in
    effect. Parses ``pip config list``; any ``*.target`` / ``*.prefix`` / ``*.root``
    key raises :class:`AgentPipError`. ``user`` is handled by the env override and
    post-verify, and index/proxy/credential keys are intentionally ignored."""
    try:
        proc = subprocess.run(
            [target_python, "-m", "pip", "config", "list"],
            capture_output=True, timeout=_CONFIG_TIMEOUT, env=env,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise AgentPipError("target_contract", "Install target could not be established") from exc
    if proc.returncode != 0:
        raise AgentPipError("target_contract", "Install target could not be established")
    for raw in proc.stdout.decode("utf-8", "replace").splitlines():
        line = raw.strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip().strip("'\"")
        if not value:
            continue
        if any(key.endswith(suf) for suf in _REDIRECT_CONFIG_SUFFIXES):
            raise AgentPipError("target_contract", "Install target could not be established")


# Import-free probe of the effective install SCHEME in the target interpreter.
# Mirrors pip's own writability test (create+delete a temp file). Reports raw
# observations; the parent decides.
_INSTALL_SCHEME_PROBE = r'''
import sys, sysconfig, site, os, json, tempfile
purelib = sysconfig.get_path("purelib")
platlib = sysconfig.get_path("platlib")
in_venv = (sys.prefix != getattr(sys, "base_prefix", sys.prefix)) or hasattr(sys, "real_prefix")
def writable(d):
    if not d or not os.path.isdir(d):
        return False
    try:
        f = tempfile.NamedTemporaryFile(dir=d, prefix=".anwrite-", delete=True)
        f.close()
        return True
    except OSError:
        return False
try:
    user_site = site.getusersitepackages()
except Exception:
    user_site = None
print(json.dumps({
    "purelib": purelib, "platlib": platlib, "in_venv": in_venv,
    "purelib_writable": writable(purelib), "platlib_writable": writable(platlib),
    "user_site": user_site,
    "pythonnousersite": os.environ.get("PYTHONNOUSERSITE"),
    "pip_user": os.environ.get("PIP_USER"),
}))
'''


def _install_scheme_ok(probe: dict) -> bool:
    """Pure decision: pip will write ONLY into an allowed, writable purelib/platlib.

    Both install roots must be writable. If they are not, pip would either error or
    (absent our ``PIP_USER=0``) default to a user-site install — a root the
    environment lock does not cover. We therefore refuse BEFORE quarantine. A venv
    (writable site) and a system interpreter with a writable site both pass; a
    non-writable system site is refused.
    """
    return bool(probe.get("purelib_writable")) and bool(probe.get("platlib_writable"))


def assert_install_scheme(target_python: str, env: dict[str, str]) -> None:
    """Refuse (pre-quarantine) if pip would not install cleanly into a writable
    purelib/platlib under the controlled env (no automatic user-site fallback)."""
    try:
        proc = subprocess.run(
            [target_python, "-c", _INSTALL_SCHEME_PROBE],
            capture_output=True, timeout=_CONFIG_TIMEOUT, env=env,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise AgentPipError("install_scheme", "Install target could not be established") from exc
    if proc.returncode != 0:
        raise AgentPipError("install_scheme", "Install target could not be established")
    try:
        probe = json.loads(proc.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AgentPipError("install_scheme", "Install target could not be established") from exc
    if not _install_scheme_ok(probe):
        raise AgentPipError("install_scheme", "Install target could not be established")


def _run_pip(args: list[str], env: dict[str, str], code: str) -> None:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=_PIP_TIMEOUT, env=env)
    except subprocess.TimeoutExpired as exc:
        raise AgentPipError(code, "Agent install step failed") from exc
    if proc.returncode != 0:
        raise AgentPipError(code, "Agent install step failed")


def pip_install_wheel(target_python: str, wheel_path: Path, env: dict[str, str]) -> None:
    """The empirically-validated two-step install (both under the environment lock,
    after quarantine). Step 1 installs/updates the wheel's declared dependencies
    (processed even at an unchanged agent version); step 2 force-reinstalls the
    exact agent wheel bytes without re-touching dependencies."""
    wheel = str(wheel_path)
    _run_pip([target_python, "-m", "pip", "install", wheel], env, "pip_step1")
    _run_pip(
        [target_python, "-m", "pip", "install", "--force-reinstall", "--no-deps", wheel],
        env, "pip_step2",
    )


# ---------------------------------------------------------------------------
# Import-free post-verify (§16)
# ---------------------------------------------------------------------------

# Runs in the TARGET interpreter. Imports only stdlib + importlib.metadata — never
# the agent's modules. Emits raw observations; the parent makes the decision.
_POSTVERIFY_PROBE = r'''
import sys, os, json, re, posixpath
import importlib.metadata as im

expected_name = sys.argv[1]
expected_top = sys.argv[2]
roots = json.loads(sys.argv[3])   # list of allowed canonical roots (purelib, platlib)

def norm_name(n):
    return re.sub(r"[-_.]+", "-", (n or "")).strip().lower()

def canon(p):
    try:
        return os.path.normcase(os.path.realpath(p))
    except Exception:
        return None

def under_allowed(path):
    cp = canon(path)
    if not cp:
        return False
    for r in roots:
        rc = os.path.normcase(r)
        if cp == rc or cp.startswith(rc + os.sep):
            return True
    return False

def top_levels(dist):
    tops = set()
    try:
        txt = dist.read_text("top_level.txt")
    except Exception:
        txt = None
    if txt:
        for line in txt.splitlines():
            line = line.strip()
            if line:
                tops.add(line.split("/")[0].split("\\")[0])
    files = None
    try:
        files = dist.files
    except Exception:
        files = None
    if files:
        for f in files:
            s = str(f).replace("\\", "/")
            if s.endswith(".dist-info/RECORD") or ".dist-info/" in s or ".data/" in s:
                continue
            parts = s.split("/")
            if len(parts) >= 2 and parts[1] == "__init__.py":
                tops.add(parts[0])
            elif len(parts) == 1 and parts[0].endswith(".py"):
                tops.add(parts[0][:-3])
    return tops

def is_editable(dist):
    try:
        raw = dist.read_text("direct_url.json")
        if raw:
            info = json.loads(raw)
            if info.get("dir_info", {}).get("editable"):
                return True
    except Exception:
        pass
    return False

def dist_info_path(dist):
    p = getattr(dist, "_path", None)
    return str(p) if p is not None else None

matches = []
other_top_providers = []
for dist in im.distributions():
    try:
        nm = norm_name(dist.metadata["Name"])
    except Exception:
        nm = None
    tops = top_levels(dist)
    if nm == expected_name:
        try:
            files = dist.files
            has_record = files is not None
        except Exception:
            has_record = False
        dip = dist_info_path(dist)
        matches.append({
            "version": dist.version,
            "dist_info_path_present": dip is not None,
            "under_allowed_root": under_allowed(dip) if dip else False,
            "editable": is_editable(dist),
            "has_record": has_record,
            "provides_top": expected_top in tops,
        })
    else:
        if expected_top in tops:
            other_top_providers.append(nm or "?")

print(json.dumps({"matches": matches, "other_top_providers": other_top_providers}))
'''


@dataclass(frozen=True)
class PostVerifyResult:
    distribution: str
    version: str


def post_verify(
    target_python: str,
    *,
    expected_name: str,
    expected_version: str,
    expected_top_level: str,
    allowed_roots: list[str],
    env: dict[str, str],
) -> PostVerifyResult:
    """Import-free confirmation, in the target interpreter, that the distribution
    installed correctly and uniquely. Raises :class:`AgentPipError` (neutral,
    path-free) on any deviation; the caller keeps the entry absent on failure."""
    try:
        proc = subprocess.run(
            [target_python, "-c", _POSTVERIFY_PROBE,
             expected_name, expected_top_level, json.dumps(allowed_roots)],
            capture_output=True, timeout=_CONFIG_TIMEOUT, env=env,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise AgentPipError("postverify", "Installed distribution could not be verified") from exc
    if proc.returncode != 0:
        raise AgentPipError("postverify", "Installed distribution could not be verified")
    try:
        data = json.loads(proc.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AgentPipError("postverify", "Installed distribution could not be verified") from exc

    matches = data.get("matches") or []
    others = data.get("other_top_providers") or []
    if len(matches) != 1:
        raise AgentPipError("postverify", "Installed distribution could not be verified")
    m = matches[0]
    try:
        version_ok = Version(m.get("version", "")) == Version(expected_version)
    except InvalidVersion:
        version_ok = False
    if not (
        version_ok
        and m.get("dist_info_path_present")
        and m.get("under_allowed_root")
        and not m.get("editable")
        and m.get("has_record")
        and m.get("provides_top")
        and not others
    ):
        raise AgentPipError("postverify", "Installed distribution could not be verified")
    return PostVerifyResult(distribution=expected_name, version=expected_version)
