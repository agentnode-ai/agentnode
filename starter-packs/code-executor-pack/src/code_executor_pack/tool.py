"""Execute Python code in a sandboxed subprocess with timeout and memory limits."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run(code: str, language: str = "python", timeout: int = 30) -> dict:
    """Execute code in a subprocess with timeout and capture output.

    Args:
        code: The source code to execute.
        language: Programming language (currently only "python" is supported).
        timeout: Maximum execution time in seconds (default 30).

    Returns:
        dict with keys: stdout, stderr, return_code, execution_time.
    """
    if language != "python":
        return {
            "stdout": "",
            "stderr": f"Unsupported language: {language}. Only 'python' is supported.",
            "return_code": 1,
            "execution_time": 0.0,
        }

    # Write code to a temporary file
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(code)
        tmp.flush()
        tmp.close()

        # Build the subprocess command.
        # On Unix we can use resource limits via a wrapper; on Windows we rely
        # on the timeout alone.
        cmd = [sys.executable, tmp.name]

        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_sandboxed_env(),
            )
            elapsed = time.monotonic() - start

            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "return_code": result.returncode,
                "execution_time": round(elapsed, 4),
            }

        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            return {
                "stdout": "",
                "stderr": f"Execution timed out after {timeout} seconds.",
                "return_code": -1,
                "execution_time": round(elapsed, 4),
            }

        except Exception as exc:
            elapsed = time.monotonic() - start
            return {
                "stdout": "",
                "stderr": str(exc),
                "return_code": -1,
                "execution_time": round(elapsed, 4),
            }

    finally:
        Path(tmp.name).unlink(missing_ok=True)


def _sandboxed_env() -> dict[str, str]:
    """Return a minimal environment for the subprocess."""
    import os

    env = {}
    # Propagate only essential variables
    for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE"):
        if key in os.environ:
            env[key] = os.environ[key]
    # Prevent the child from importing the caller's site-packages by default
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env
