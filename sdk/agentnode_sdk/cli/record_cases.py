"""Record VCR cassettes for verification cases."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def record_cases(pkg_path: Path, manifest: dict) -> dict:
    """Record VCR cassettes for all fixture-mode cases.

    Returns: {"recorded": [...], "skipped": [...], "errors": [...]}
    """
    verification = manifest.get("verification", {})
    if not isinstance(verification, dict):
        return {"recorded": [], "skipped": [], "errors": ["No verification section in manifest"]}

    cases = verification.get("cases", [])
    if not cases:
        return {"recorded": [], "skipped": [], "errors": ["No verification.cases defined"]}

    fixture_cases = [c for c in cases if isinstance(c, dict) and c.get("cassette")]
    if not fixture_cases:
        return {"recorded": [], "skipped": [], "errors": [
            "No cases with cassette paths found. "
            "Add 'cassette: fixtures/cassettes/<name>.yaml' to your cases."
        ]}

    entrypoint = manifest.get("entrypoint", "")
    tools = (manifest.get("capabilities", {}) or {}).get("tools", [])

    # Install package in temp venv for recording
    venv_path = Path(tempfile.mkdtemp(prefix="anp_record_")) / "venv"
    python = _setup_venv(pkg_path, venv_path)
    if not python:
        return {"recorded": [], "skipped": [], "errors": ["Failed to create venv or install package"]}

    recorded = []
    skipped = []
    errors = []

    for case in fixture_cases:
        name = case.get("name", "unnamed")
        cassette = case.get("cassette", "")
        case_input = case.get("input", {})
        tool_filter = case.get("tool")

        # Resolve function to call
        module_path, func_name = _resolve_entrypoint(entrypoint, tools, tool_filter)

        # Ensure cassette directory exists
        cassette_abs = pkg_path / cassette
        cassette_abs.parent.mkdir(parents=True, exist_ok=True)

        # Build recording script
        code = _build_record_code(module_path, func_name, case_input, str(cassette_abs))

        try:
            r = subprocess.run(
                [python, "-c", code],
                capture_output=True, text=True, timeout=30,
                cwd=str(pkg_path),
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )

            if r.returncode != 0:
                err_msg = r.stderr.strip().split("\n")[-1] if r.stderr.strip() else "Unknown error"
                errors.append(f"{name}: {err_msg}")
                continue

            # Parse output
            output = r.stdout.strip()
            if "RECORD_OK" in output:
                recorded.append({"name": name, "cassette": cassette})
            elif "RECORD_SKIP" in output:
                reason = output.split("RECORD_SKIP:")[-1].strip() if "RECORD_SKIP:" in output else "unknown"
                skipped.append({"name": name, "reason": reason})
            else:
                errors.append(f"{name}: No confirmation output — check tool behavior")

        except subprocess.TimeoutExpired:
            errors.append(f"{name}: Timeout (30s) — API call took too long")
        except Exception as e:
            errors.append(f"{name}: {e}")

    # Cleanup venv
    import shutil
    shutil.rmtree(venv_path.parent, ignore_errors=True)

    return {"recorded": recorded, "skipped": skipped, "errors": errors}


def check_manifest_in(pkg_path: Path) -> tuple[bool, str]:
    """Check if MANIFEST.in exists and includes fixtures."""
    manifest_in = pkg_path / "MANIFEST.in"
    if not manifest_in.exists():
        return False, "MANIFEST.in not found — create it with: recursive-include fixtures *"
    content = manifest_in.read_text(encoding="utf-8", errors="ignore")
    if "fixtures" not in content:
        return False, "MANIFEST.in does not include fixtures — add: recursive-include fixtures *"
    return True, "OK"


def ensure_manifest_in(pkg_path: Path) -> bool:
    """Create or update MANIFEST.in to include fixtures. Returns True if modified."""
    manifest_in = pkg_path / "MANIFEST.in"
    if manifest_in.exists():
        content = manifest_in.read_text(encoding="utf-8", errors="ignore")
        if "fixtures" in content:
            return False
        with open(manifest_in, "a", encoding="utf-8") as f:
            f.write("\nrecursive-include fixtures *\n")
        return True
    else:
        manifest_in.write_text("recursive-include fixtures *\ninclude agentnode.yaml\n", encoding="utf-8")
        return True


def _setup_venv(pkg_path: Path, venv_path: Path) -> str | None:
    """Create venv with package + vcrpy installed."""
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            capture_output=True, text=True, timeout=60,
        )
        python = _venv_python(venv_path)
        r = subprocess.run(
            [python, "-m", "pip", "install", "-q", str(pkg_path), "vcrpy"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            return None
        return python
    except Exception:
        return None


def _venv_python(venv_path: Path) -> str:
    if sys.platform == "win32":
        return str(venv_path / "Scripts" / "python.exe")
    return str(venv_path / "bin" / "python")


def _resolve_entrypoint(entrypoint: str, tools: list[dict], tool_filter: str | None) -> tuple[str, str]:
    """Resolve module path and function name."""
    module_path = entrypoint.split(":")[0] if ":" in entrypoint else entrypoint
    func_name = "run"

    if tool_filter and tools:
        for t in tools:
            if t.get("name") == tool_filter:
                ep = t.get("entrypoint", "")
                if ":" in ep:
                    module_path, func_name = ep.rsplit(":", 1)
                break

    return module_path, func_name


def _build_record_code(module_path: str, func_name: str, case_input: dict, cassette_path: str) -> str:
    """Build Python code that records a VCR cassette."""
    input_json = json.dumps(case_input)
    return f"""\
import json, sys
try:
    import vcr
except ImportError:
    print("RECORD_SKIP: vcrpy not installed")
    sys.exit(0)

from {module_path} import {func_name}

_vcr = vcr.VCR(
    record_mode="new_episodes",
    match_on=["method", "uri"],
    filter_headers=["Authorization", "X-Api-Key", "api-key"],
)

_input = json.loads({input_json!r})
_cassette_path = {cassette_path!r}

try:
    with _vcr.use_cassette(_cassette_path):
        _result = {func_name}(**_input)
    print("RECORD_OK")
except Exception as e:
    print(f"RECORD_SKIP: {{type(e).__name__}}: {{str(e)[:200]}}")
"""
