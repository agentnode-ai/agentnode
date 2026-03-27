"""
@tool function without a return type annotation.
People often skip the return annotation — the body still returns a dict.
"""

import json
import subprocess

from langchain.tools import tool


@tool
def run_shell_command(command: str):
    """
    Run a shell command and return its output.

    WARNING: Use only in sandboxed environments.

    Args:
        command: Shell command string to execute

    Returns a dict with stdout, stderr, returncode.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,  # noqa: S602
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {
            "command": command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "stdout": "",
            "stderr": "Command timed out after 30 seconds",
            "returncode": -1,
            "success": False,
        }
    except Exception as e:
        return {
            "command": command,
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
            "success": False,
        }
