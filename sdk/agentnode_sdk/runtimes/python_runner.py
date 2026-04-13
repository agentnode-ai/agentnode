"""Python tool execution — direct (in-process) or subprocess (isolated).

Extracted from ``runner.py`` to support the multi-runtime dispatcher.
All original logic is preserved; the public entry point is ``run_python()``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

# P1-SDK7: serialize concurrent callers of _run_direct so the
# os.environ["AGENTNODE_LOCKFILE"] save/restore dance is atomic.
# Concurrent calls from different threads would otherwise race the
# environment variable and leak one another's lockfile paths.
_DIRECT_ENV_LOCK = threading.Lock()

from agentnode_sdk.exceptions import AgentNodeToolError
from agentnode_sdk.installer import load_tool, read_lockfile
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
# P1-SDK8: slug and tool_name are passed on stdin alongside kwargs, not
# format-injected into the script source. Previously any slug/tool_name
# containing escape sequences, braces, or weird Unicode could either
# break `.format()` or inject arbitrary Python via `repr()`. The wrapper
# is now a pure static string — no `.format()` substitution happens.
_SUBPROCESS_WRAPPER = '''\
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
    _slug = _payload["slug"]
    _tool_name = _payload.get("tool_name")
    kwargs = _payload.get("kwargs") or {}

    from agentnode_sdk.installer import load_tool

    func = load_tool(_slug, tool_name=_tool_name)

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_python(
    slug: str,
    tool_name: str | None,
    *,
    mode: str = "auto",
    timeout: float = 30.0,
    entry: dict | None = None,
    lockfile_path: Path | None = None,
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
    # Resolve auto-mode
    resolved = mode
    if mode == "auto":
        trust = _get_trust_level(slug, lockfile_path)
        resolved = _resolve_mode(mode, trust)

    t0 = time.monotonic()
    try:
        if resolved == "direct":
            result = _run_direct(slug, tool_name, kwargs, lockfile_path)
            elapsed = (time.monotonic() - t0) * 1000
            return RunToolResult(
                success=True,
                result=result,
                mode_used="direct",
                duration_ms=round(elapsed, 1),
            )
        else:
            result, error, timed_out = _run_subprocess(
                slug, tool_name, kwargs, timeout, lockfile_path,
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
    slug: str,
    tool_name: str | None,
    kwargs: dict,
    lockfile_path: Path | None,
) -> Any:
    """Load and call the tool in the current process.

    P1-SDK7: the env-var mutation is guarded by ``_DIRECT_ENV_LOCK`` so
    concurrent callers from different threads can't clobber each other's
    ``AGENTNODE_LOCKFILE``. Note this still does NOT make the ACTUAL tool
    call concurrency-safe — two threads loading different tools that
    share a module will still interleave inside the tool. The lock only
    covers the env-var save/restore and the ``load_tool()`` lookup.
    """
    if lockfile_path is None:
        # No lockfile override — env doesn't need touching, no lock needed.
        try:
            func = load_tool(slug, tool_name=tool_name)
            return func(**kwargs)
        except ImportError as exc:
            raise AgentNodeToolError(str(exc), tool_name=tool_name or slug) from exc

    with _DIRECT_ENV_LOCK:
        old_env = os.environ.get("AGENTNODE_LOCKFILE")
        os.environ["AGENTNODE_LOCKFILE"] = str(lockfile_path)
        try:
            func = load_tool(slug, tool_name=tool_name)
        except ImportError as exc:
            raise AgentNodeToolError(str(exc), tool_name=tool_name or slug) from exc
        finally:
            if old_env is None:
                os.environ.pop("AGENTNODE_LOCKFILE", None)
            else:
                os.environ["AGENTNODE_LOCKFILE"] = old_env
    # Run the tool OUTSIDE the lock — holding it across user code would
    # serialize every direct-mode run_tool call unnecessarily.
    return func(**kwargs)


# ---------------------------------------------------------------------------
# Subprocess execution
# ---------------------------------------------------------------------------

def _run_subprocess(
    slug: str,
    tool_name: str | None,
    kwargs: dict,
    timeout: float,
    lockfile_path: Path | None,
) -> tuple[Any, str | None, bool]:
    """Run tool in an isolated child process.

    Returns ``(result, error_message, timed_out)``.
    """
    tmpdir = tempfile.mkdtemp(prefix="agentnode-run-")
    try:
        # P1-SDK8: wrapper is a static string; slug/tool_name travel via
        # stdin alongside kwargs. No `.format()` substitution happens.
        script = _SUBPROCESS_WRAPPER
        input_json = json.dumps({
            "slug": slug,
            "tool_name": tool_name,
            "kwargs": kwargs,
        })

        env = _filtered_env()
        # Point subprocess at the real lockfile (cwd will be tmpdir)
        lf_path = str(lockfile_path) if lockfile_path else str(Path.cwd() / "agentnode.lock")
        env["AGENTNODE_LOCKFILE"] = lf_path

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
