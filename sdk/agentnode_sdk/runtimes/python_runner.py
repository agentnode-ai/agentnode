"""Python tool execution — direct (in-process) or subprocess (isolated).

Extracted from ``runner.py`` to support the multi-runtime dispatcher.
All original logic is preserved; the public entry point is ``run_python()``.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from agentnode_sdk.exceptions import AgentNodeToolError
from agentnode_sdk.installer import (
    _load_entrypoint_from_entry,
    _resolve_entrypoint_from_entry,
    read_lockfile,
)
from agentnode_sdk.models import RunToolResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TRUSTED_LEVELS = {"trusted", "curated"}  # retained for display/logging
# P0-06: ``mode='auto'`` must ALWAYS resolve to subprocess so that the
# documented isolation guarantee is true by default, independent of trust
# level. ``mode='direct'`` remains an explicit opt-in for performance-
# critical workloads that knowingly share in-process globals.
_DIRECT_TRUST_LEVELS: set[str] = set()

# Environment variables safe to pass to the subprocess.
# Allowlist approach: anything not listed is stripped.
_ENV_ALLOWLIST = {
    # Core
    "PATH", "HOME", "USERPROFILE", "USER", "LOGNAME",
    # Python
    "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    # Windows required
    "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "WINDIR", "PATHEXT",
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "COMMONPROGRAMFILES",
    # Temp
    "TEMP", "TMP", "TMPDIR",
    # Locale
    "LANG", "LC_ALL", "LC_CTYPE",
    # AgentNode internal
    "AGENTNODE_LOCKFILE",
}

# Wrapper script executed inside the subprocess.
#
# 0.3A: the child NEVER reads the lockfile and NEVER calls load_tool. The parent
# (post run_tool gate) resolves the entrypoint STRING-ONLY from the already-gated
# entry and passes (module, [candidate functions]) on stdin; the child only does
# importlib + getattr. This removes the second lockfile read and the entry-
# substitution / TOCTOU window (a file change after the parent's gate can no
# longer swap the imported entry). Same shape as _CONTAINER_WRAPPER.
#
# P1-SDK8: all inputs travel via stdin JSON — the wrapper is a pure static string,
# no `.format()` substitution.
_SUBPROCESS_WRAPPER = '''\
import importlib
import io
import json
import sys

def _safe_serialize(obj):
    """JSON-serialize *obj*, falling back to repr for non-serializable types."""
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError, OverflowError):
        return {"__agentnode_fallback_repr__": True, "repr": repr(obj)[:2000]}

try:
    _payload = json.loads(sys.stdin.read())
    _module = _payload["module"]
    _functions = _payload.get("functions") or []
    kwargs = _payload.get("kwargs") or {}

    mod = importlib.import_module(_module)
    func = None
    for _name in _functions:
        cand = getattr(mod, _name, None)
        if callable(cand):
            func = cand
            break
    if func is None:
        raise ImportError(
            "none of the candidate functions " + repr(_functions)
            + " found in module '" + _module + "'"
        )

    # Capture stdout so tool print() calls don't corrupt our JSON output.
    captured = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = captured
    try:
        result = func(**kwargs)
    finally:
        sys.stdout = real_stdout

    logs = captured.getvalue()
    payload = {"ok": True, "result": _safe_serialize(result)}
    if logs:
        payload["logs"] = logs[:10000]
    json.dump(payload, real_stdout)

except Exception as exc:
    # Write error as JSON so the parent always gets parseable output.
    json.dump(
        {"ok": False, "error": type(exc).__name__ + ": " + str(exc)},
        sys.__stdout__,
    )
'''


# P0.3: SDK-free wrapper executed INSIDE the container. The container image has
# python + the built package on PYTHONPATH (the mounted volume) but NOT the
# AgentNode SDK and NOT the lockfile. So this wrapper cannot call load_tool();
# the host resolves the entrypoint to (module, [candidate functions]) STRING-ONLY
# (no import) and passes them on stdin. The container only does importlib +
# getattr. Output shape matches _SUBPROCESS_WRAPPER ({ok, result, logs}).
_CONTAINER_WRAPPER = '''\
import importlib
import io
import json
import sys

def _safe_serialize(obj):
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError, OverflowError):
        return {"__agentnode_fallback_repr__": True, "repr": repr(obj)[:2000]}

try:
    _payload = json.loads(sys.stdin.read())
    _module = _payload["module"]
    _functions = _payload.get("functions") or []
    kwargs = _payload.get("kwargs") or {}

    mod = importlib.import_module(_module)
    func = None
    for _name in _functions:
        cand = getattr(mod, _name, None)
        if callable(cand):
            func = cand
            break
    if func is None:
        raise ImportError(
            "none of the candidate functions " + repr(_functions)
            + " found in module '" + _module + "'"
        )

    captured = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = captured
    try:
        result = func(**kwargs)
    finally:
        sys.stdout = real_stdout

    logs = captured.getvalue()
    payload = {"ok": True, "result": _safe_serialize(result)}
    if logs:
        payload["logs"] = logs[:10000]
    json.dump(payload, real_stdout)

except Exception as exc:
    json.dump(
        {"ok": False, "error": type(exc).__name__ + ": " + str(exc)},
        sys.__stdout__,
    )
'''


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_python(
    slug: str,
    tool_name: str | None,
    *,
    _host_policy_decision: Any,
    mode: str = "auto",
    timeout: float = 30.0,
    entry: dict | None = None,
    lockfile_path: Path | None = None,
    consent_callback=None,
    **kwargs: Any,
) -> RunToolResult:
    """Run a Python tool in direct or subprocess mode.

    Args:
        slug: Package slug (e.g. ``"csv-analyzer-pack"``).
        tool_name: Tool name for multi-tool v0.2 packs.
        mode: ``"direct"``, ``"subprocess"``, or ``"auto"`` (choose based on trust level).
        timeout: Maximum wall-clock seconds for subprocess mode.
        entry: Lockfile entry dict (optional, used by dispatcher).
        lockfile_path: Override path to ``agentnode.lock``.
        **kwargs: Arguments forwarded to the tool function.

    Returns:
        :class:`RunToolResult` with execution details.
    """
    # P0.3: community (sandbox-required) toolpacks run in an ephemeral container
    # that mounts ONLY the pre-built per-pack-version volume (read-only). This is
    # checked BEFORE mode resolution so an explicit mode='direct' can NEVER bypass
    # isolation for community code. curated/trusted fall through to the host path.
    # Missing/None/unknown trust → sandbox-required (never host), mirroring the
    # runner.run_tool gate and policy.requires_sandbox.
    from agentnode_sdk.sandbox import SandboxRequiredError
    from agentnode_sdk.sandbox.policy import HostTrustPolicyDecision
    from agentnode_sdk.runtimes.toolpack_credentials import (
        missing_env_message,
        missing_required_env,
    )
    # run_python is reached ONLY through the run_tool gate, which passes the
    # already-gated entry. There is NO fallback lockfile read: without a gated
    # snapshot there is no integrity report to honour, so a missing / non-object
    # entry is refused UP FRONT — before credentials, mode resolution, import,
    # subprocess, or container — with no side effect and no invented decision.
    if not isinstance(entry, dict):
        return RunToolResult(
            success=False,
            error=(
                f"run_python requires an already-gated lockfile entry for '{slug}'; "
                "none was provided."
            ),
            mode_used="no_entry",
        )

    # F1: the host-trust policy decision is OWNED by run_tool and passed in
    # immutably — run_python never re-reads host_trust_policy. Refuse (before
    # credentials, mode, import, subprocess, or container) a missing or entry-
    # mismatched decision; no config fallback, no re-derivation.
    if not isinstance(_host_policy_decision, HostTrustPolicyDecision):
        return RunToolResult(
            success=False,
            error=f"run_python requires a host-trust policy decision for '{slug}'; none was provided.",
            mode_used="no_entry",
        )
    if _host_policy_decision.trust_level != (entry.get("trust_level") or ""):
        return RunToolResult(
            success=False,
            error="host-trust policy decision does not match entry trust level",
            mode_used="no_entry",
        )

    # Mode/trust decisions use THIS gated entry only — never a second lockfile read.
    dispatch_trust = entry.get("trust_level")

    # Declared-credentials gate (names/presence only — no value is read): a pack
    # whose required env_requirements are not set fails HERE with an actionable
    # message instead of a cryptic tool error deep inside the run. Applies to
    # host and sandbox paths alike.
    _missing_creds = missing_required_env(entry)
    if _missing_creds:
        return RunToolResult(
            success=False,
            error=missing_env_message(slug, _missing_creds),
            mode_used="credentials_missing",
        )
    # host-trust policy: "default" = curated/trusted on host; "curated_only"/"none"
    # route trusted (and curated) into the sandbox. The decision was made ONCE by
    # the owner from the same gated entry — consumed here, never recomputed —
    # BEFORE mode resolution, so an explicit mode='direct' can never bypass it.
    if _host_policy_decision.sandbox_required:
        t0 = time.monotonic()
        try:
            result, error, timed_out = _run_container(
                slug, tool_name, kwargs, timeout, entry,
                consent_callback=consent_callback,
            )
            elapsed = (time.monotonic() - t0) * 1000
            return RunToolResult(
                success=error is None,
                result=result,
                error=error,
                mode_used="sandbox",
                duration_ms=round(elapsed, 1),
                timed_out=timed_out,
            )
        except SandboxRequiredError as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return RunToolResult(
                success=False,
                error=str(exc),
                mode_used="sandbox_unavailable",
                duration_ms=round(elapsed, 1),
            )

    # Resolve auto-mode (no second lockfile read — reuse the entry's trust level).
    resolved = mode
    if mode == "auto":
        resolved = _resolve_mode(mode, dispatch_trust)

    if resolved == "direct" and mode == "direct":
        logger.warning(
            "Running %s in direct mode — full env access, no isolation. "
            "Use mode='auto' (default) for subprocess isolation.",
            slug,
        )

    t0 = time.monotonic()
    try:
        if resolved == "direct":
            result = _run_direct(entry, slug, tool_name, kwargs)
            elapsed = (time.monotonic() - t0) * 1000
            return RunToolResult(
                success=True,
                result=result,
                mode_used="direct",
                duration_ms=round(elapsed, 1),
            )
        else:
            result, error, timed_out = _run_subprocess(
                entry, slug, tool_name, kwargs, timeout,
            )
            elapsed = (time.monotonic() - t0) * 1000
            return RunToolResult(
                success=error is None,
                result=result,
                error=error,
                mode_used="subprocess",
                duration_ms=round(elapsed, 1),
                timed_out=timed_out,
            )
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        return RunToolResult(
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            mode_used=resolved,
            duration_ms=round(elapsed, 1),
        )


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------

def _resolve_mode(mode: str, trust_level: str | None) -> str:
    """Map ``"auto"`` to a concrete execution mode.

    ``auto`` always resolves to ``subprocess`` regardless of trust level,
    so the isolation guarantee documented in the SDK README holds by
    default. Callers that want in-process execution must pass
    ``mode="direct"`` explicitly.
    """
    if mode != "auto":
        return mode
    return "subprocess"


def _get_trust_level(slug: str, lockfile_path: Path | None) -> str | None:
    """Read trust_level from the lockfile for *slug*."""
    data = read_lockfile(lockfile_path)
    pkg = data.get("packages", {}).get(slug)
    if not pkg:
        return None
    return pkg.get("trust_level")


# ---------------------------------------------------------------------------
# Direct execution
# ---------------------------------------------------------------------------

def _run_direct(
    entry: dict,
    slug: str,
    tool_name: str | None,
    kwargs: dict,
) -> Any:
    """Load and call the tool in the current process, using the ALREADY-GATED
    entry from the run_tool snapshot.

    0.3A: this no longer calls ``load_tool`` and reads NO lockfile — the entry is
    the object the run_tool gate already evaluated, so there is no second read, no
    second integrity gate, and no entry-substitution window. (The old
    ``AGENTNODE_LOCKFILE`` env dance existed only for ``load_tool``'s lookup and is
    therefore gone.)
    """
    try:
        func = _load_entrypoint_from_entry(entry, slug, tool_name)
    except ImportError as exc:
        raise AgentNodeToolError(str(exc), tool_name=tool_name or slug) from exc
    return func(**kwargs)


# ---------------------------------------------------------------------------
# Subprocess execution
# ---------------------------------------------------------------------------

def _run_subprocess(
    entry: dict,
    slug: str,
    tool_name: str | None,
    kwargs: dict,
    timeout: float,
) -> tuple[Any, str | None, bool]:
    """Run tool in an isolated child process, using the ALREADY-GATED entry.

    0.3A: the parent resolves the entrypoint STRING-ONLY from the gated entry (no
    import) and passes only ``(module, functions)`` on stdin. The child reads NO
    lockfile and never calls ``load_tool`` — so a lockfile change after the gate
    cannot substitute the imported entry (no TOCTOU), and there is no second read.

    Returns ``(result, error_message, timed_out)``.
    """
    try:
        module_path, functions = _resolve_entrypoint_from_entry(entry, slug, tool_name)
    except ImportError as exc:
        return None, f"ImportError: {exc}", False

    tmpdir = tempfile.mkdtemp(prefix="agentnode-run-")
    try:
        # P1-SDK8: wrapper is a static string; the resolved module/functions travel
        # via stdin alongside kwargs. No `.format()` substitution happens.
        script = _SUBPROCESS_WRAPPER
        input_json = json.dumps({
            "module": module_path,
            "functions": functions,
            "kwargs": kwargs,
        })

        # The child imports by module name and reads NO lockfile, so the lockfile
        # path must never cross the process boundary. _filtered_env() allowlists
        # AGENTNODE_LOCKFILE, so explicitly drop any inherited value here.
        env = _filtered_env()
        env.pop("AGENTNODE_LOCKFILE", None)

        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tmpdir,
            env=env,
            text=True,
        )

        try:
            stdout, stderr = proc.communicate(input=input_json, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return None, f"Tool timed out after {timeout}s", True

        if proc.returncode != 0:
            return None, f"Tool exited with code {proc.returncode}: {stderr.strip()[:2000]}", False

        if not stdout.strip():
            return None, f"Tool produced no output. stderr: {stderr.strip()[:2000]}", False

        try:
            output = json.loads(stdout)
        except json.JSONDecodeError:
            return None, f"Invalid JSON from tool: {stdout[:500]}", False

        if output.get("ok"):
            return output.get("result"), None, False
        else:
            return None, output.get("error", "Unknown error"), False

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _filtered_env() -> dict[str, str]:
    """Build a safe environment for subprocess execution.

    Uses an allowlist -- anything not listed is stripped.
    This prevents leaking API keys, tokens, and cloud credentials.
    """
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}


# ---------------------------------------------------------------------------
# Container execution (P0.3) — community toolpacks in an ephemeral container
# ---------------------------------------------------------------------------

def _resolve_container_target(entry: dict, tool_name: str | None) -> tuple[str, list[str]]:
    """Resolve ``(module, [candidate_function_names])`` STRING-ONLY (no import).

    Mirrors ``installer.load_tool()``'s precedence so the container calls the same
    function the host would — but using only the lockfile strings, since the
    container has neither the SDK nor the lockfile:
      - v0.2 per-tool entrypoint match → that module + its function;
      - v0.1 (tool_name given, no ``tools`` list) → package module, try ``tool_name``
        first then the default function (``run``), matching load_tool's getattr order;
      - no tool_name → package entrypoint module + its function.
    """
    from agentnode_sdk.installer import (
        _resolve_entrypoint, _default_tool_entrypoint, _multi_tool_hint,
    )

    tools = entry.get("tools") or []
    if tool_name:
        for t in tools:
            if t.get("name") == tool_name and t.get("entrypoint"):
                module, func = _resolve_entrypoint(t["entrypoint"])
                return module, [func]
        ep = entry.get("entrypoint")
        if ep and not tools:
            module, func = _resolve_entrypoint(ep)
            cands = [tool_name] if tool_name == func else [tool_name, func]
            return module, cands
        raise AgentNodeToolError(
            f"Tool '{tool_name}' has no resolvable entrypoint in the lockfile.",
            tool_name=tool_name,
        )

    ep = _default_tool_entrypoint(entry)
    if not ep:
        raise AgentNodeToolError(
            "Package has no entrypoint in the lockfile." + _multi_tool_hint(entry),
            tool_name=None,
        )
    module, func = _resolve_entrypoint(ep)
    return module, [func]


def _run_container(
    slug: str,
    tool_name: str | None,
    kwargs: dict,
    timeout: float,
    entry: dict,
    consent_callback=None,
) -> tuple[Any, str | None, bool]:
    """Run a community toolpack inside an ephemeral container that mounts ONLY
    the pre-built per-pack-version volume (read-only). Returns
    ``(result, error_message, timed_out)``.

    Fail-closed everywhere — NEVER a host fallback:
      * volume gate: the lockfile must claim ``sandboxed`` AND the recorded
        ``sandbox_volume`` must equal the name recomputed from slug+version+hash,
        AND ``<runtime> volume inspect`` must succeed. Otherwise → reinstall error.
      * no container runtime → ``SandboxRequiredError`` (raised; the caller maps it
        to ``sandbox_unavailable``).
    Host-FS/HOME/secrets are isolated (clean HOME, only the volume mounted, no env
    passthrough). Network is derived from the declared ``network_level`` permission
    (allowlist; unknown = deny).
    """
    from agentnode_sdk.sandbox import (
        SandboxRequiredError,
        get_default_backend,
        network_for_level,
        sandbox_volume_name,
    )
    from agentnode_sdk.sandbox.types import MountSpec

    # entry is the already-gated object from run_python — never re-read here.
    _reinstall = (
        "Sandbox volume missing or stale. Reinstall this toolpack to rebuild it "
        f"in the sandbox (run: agentnode install {slug})."
    )

    # --- Volume gate: do NOT blindly trust lockfile.sandbox_volume ----------
    expected_vol = sandbox_volume_name(slug, entry.get("version"), entry.get("artifact_hash"))
    if not entry.get("sandboxed") or entry.get("sandbox_volume") != expected_vol:
        return None, _reinstall, False

    backend = get_default_backend()
    availability = backend.check_available()
    if not availability.available:
        raise SandboxRequiredError(
            "Community toolpack execution requires a container runtime (Docker or "
            f"Podman). {availability.reason or 'None detected'} — refusing to run "
            "untrusted code on the host."
        )

    runtime = availability.backend or "docker"
    try:
        insp = subprocess.run(
            [runtime, "volume", "inspect", expected_vol],
            capture_output=True, timeout=10,
        )
    except Exception as exc:
        return None, f"Could not verify sandbox volume: {exc}", False
    if insp.returncode != 0:
        return None, _reinstall, False

    # --- Resolve target + network (allowlist; unknown = deny) ---------------
    module, functions = _resolve_container_target(entry, tool_name)
    network = network_for_level((entry.get("permissions") or {}).get("network_level"))

    # --- Credentialed toolpack (declared env_requirements) ------------------
    # Secrets never ride the plain path: consent + sealed egress allowlist are
    # mandatory, the network becomes a proxied egress bound to the declared
    # domains, and the key travels by NAME only (`--env NAME`, value read by
    # the container runtime — never on argv, never in spec.env). Fail-closed:
    # any refusal aborts the run; there is NO fallback to a run without the key.
    from agentnode_sdk.runtimes.toolpack_credentials import (
        CredentialedToolpackRefused,
        declared_env_names,
        prepare_credentialed_run,
    )

    egress_handle = None
    if declared_env_names(entry):
        from agentnode_sdk.sandbox.egress import start_egress_proxy
        from agentnode_sdk.sandbox.types import ProcessSpec

        try:
            sealed, passthrough = prepare_credentialed_run(
                slug, entry, consent_callback=consent_callback
            )
        except CredentialedToolpackRefused as exc:
            return None, str(exc), False
        # Proxy from the SEALED domains only; failure ⇒ no container.
        egress_handle = start_egress_proxy(list(sealed))
        spec = ProcessSpec(
            command=["python", "-c", _CONTAINER_WRAPPER],
            network="egress",
            egress=egress_handle.spec,
            env_passthrough=list(passthrough),
            mounts=[MountSpec(src=expected_vol, dst="/pack", read_only=True)],
            env={"PYTHONPATH": "/pack"},
            clean_home=True,
            interactive=True,
        )
    else:
        spec = backend.build_process_spec(
            ["python", "-c", _CONTAINER_WRAPPER],
            network=network,
            mounts=[MountSpec(src=expected_vol, dst="/pack", read_only=True)],
            env={"PYTHONPATH": "/pack"},
            clean_home=True,
            interactive=True,  # -i so the runtime forwards our stdin JSON payload
        )
    input_json = json.dumps({"module": module, "functions": functions, "kwargs": kwargs})

    try:
        returncode, stdout, stderr = backend.run_process(
            spec, input_text=input_json, timeout=timeout,
        )
    finally:
        if egress_handle is not None:
            try:
                from agentnode_sdk.sandbox.egress import stop_egress_proxy

                stop_egress_proxy(egress_handle)
            except Exception:
                pass
    if returncode == -1:
        return None, f"Tool timed out after {timeout}s", True
    if returncode != 0:
        return None, f"Sandbox exited with code {returncode}: {stderr.strip()[:2000]}", False
    if not stdout.strip():
        return None, f"Tool produced no output. stderr: {stderr.strip()[:2000]}", False
    try:
        output = json.loads(stdout)
    except json.JSONDecodeError:
        return None, f"Invalid JSON from sandbox: {stdout[:500]}", False
    if output.get("ok"):
        return output.get("result"), None, False
    return None, output.get("error", "Unknown error"), False
