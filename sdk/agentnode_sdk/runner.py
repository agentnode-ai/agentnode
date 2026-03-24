"""Tool execution with optional subprocess isolation.

Provides ``run_tool()`` — the main entry point for running installed
AgentNode tools in either *direct* (in-process) or *subprocess*
(isolated child process) mode.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from agentnode_sdk.exceptions import AgentNodeToolError
from agentnode_sdk.installer import load_tool, read_lockfile
from agentnode_sdk.models import RunToolResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TRUSTED_LEVELS = {"trusted", "curated"}
_DIRECT_TRUST_LEVELS = _TRUSTED_LEVELS  # auto-mode runs these direct

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
# Uses stdin for input (JSON), writes result to stdout (JSON).
# stdout is redirected inside the tool call so print() doesn't pollute JSON.
_SUBPROCESS_WRAPPER = '''\
import io
import json
import sys
import traceback

def _safe_serialize(obj):
    """JSON-serialize *obj*, falling back to repr for non-serializable types."""
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError, OverflowError):
        return {{"__agentnode_fallback_repr__": True, "repr": repr(obj)[:2000]}}

try:
    kwargs = json.loads(sys.stdin.read())

    from agentnode_sdk.installer import load_tool

    func = load_tool({slug!r}, tool_name={tool_name!r})

    # Capture stdout so tool print() calls don't corrupt our JSON output.
    captured = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = captured
    try:
        result = func(**kwargs)
    finally:
        sys.stdout = real_stdout

    logs = captured.getvalue()
    payload = {{"ok": True, "result": _safe_serialize(result)}}
    if logs:
        payload["logs"] = logs[:10000]
    json.dump(payload, real_stdout)

except Exception as exc:
    # Write error as JSON so the parent always gets parseable output.
    json.dump(
        {{"ok": False, "error": f"{{type(exc).__name__}}: {{exc}}"}},
        sys.__stdout__,
    )
'''


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_tool(
    slug: str,
    tool_name: str | None = None,
    *,
    mode: str = "auto",
    timeout: float = 30.0,
    lockfile_path: Path | None = None,
    **kwargs: Any,
) -> RunToolResult:
    """Run an installed tool with optional process isolation.

    Args:
        slug: Package slug (e.g. ``"csv-analyzer-pack"``).
        tool_name: Tool name for multi-tool v0.2 packs.  If *None* and
            the package is a multi-tool pack, raises an error.
        mode: ``"direct"`` (in-process), ``"subprocess"`` (isolated child
            process), or ``"auto"`` (choose based on trust level).
        timeout: Maximum wall-clock seconds for subprocess mode.
        lockfile_path: Override path to ``agentnode.lock``.
        **kwargs: Arguments forwarded to the tool function.

    Returns:
        :class:`RunToolResult` with ``success``, ``result``, ``error``,
        ``mode_used``, ``duration_ms``, and ``timed_out`` fields.
    """
    if mode not in ("direct", "subprocess", "auto"):
        raise ValueError(f"Unknown mode: {mode!r}. Use 'direct', 'subprocess', or 'auto'.")

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
    """Map ``"auto"`` to a concrete mode based on trust level.

    ========== ===========
    Trust      Mode
    ========== ===========
    curated    direct
    trusted    direct
    verified   subprocess
    unverified subprocess
    None       subprocess  (backward compat with old lockfiles)
    ========== ===========
    """
    if mode != "auto":
        return mode
    if trust_level in _DIRECT_TRUST_LEVELS:
        return "direct"
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
    """Load and call the tool in the current process."""
    # If a custom lockfile path is set, make it visible to load_tool
    old_env = os.environ.get("AGENTNODE_LOCKFILE")
    if lockfile_path:
        os.environ["AGENTNODE_LOCKFILE"] = str(lockfile_path)
    try:
        func = load_tool(slug, tool_name=tool_name)
        return func(**kwargs)
    except ImportError as exc:
        raise AgentNodeToolError(str(exc), tool_name=tool_name or slug) from exc
    finally:
        if lockfile_path:
            if old_env is None:
                os.environ.pop("AGENTNODE_LOCKFILE", None)
            else:
                os.environ["AGENTNODE_LOCKFILE"] = old_env


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
        script = _SUBPROCESS_WRAPPER.format(slug=slug, tool_name=tool_name)
        input_json = json.dumps(kwargs)

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

    Uses an allowlist — anything not listed is stripped.
    This prevents leaking API keys, tokens, and cloud credentials.
    """
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}
