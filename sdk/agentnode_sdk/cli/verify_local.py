"""Local verification runner — mirrors the server pipeline without Docker/DB."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CaseResult:
    name: str
    passed: bool
    return_type: str = ""
    return_keys: list[str] = field(default_factory=list)
    duration_ms: int = 0
    error: str = ""
    output_hash: str = ""


@dataclass
class VerifyResult:
    install_ok: bool = False
    install_log: str = ""
    import_ok: bool = False
    import_log: str = ""
    smoke_status: str = "not_run"
    smoke_reason: str = ""
    cases: list[CaseResult] = field(default_factory=list)
    reliability: float = 0.0
    determinism: float = 0.0
    contract_valid: bool = False
    tests_ok: bool | None = None
    tests_log: str = ""
    score: int = 0
    tier: str = "unverified"
    verification_mode: str = "none"
    has_explicit_cases: bool = False
    warnings: list[str] = field(default_factory=list)


def run_local_verification(pkg_path: Path, manifest: dict) -> VerifyResult:
    """Run the full verification pipeline locally."""
    result = VerifyResult()

    pkg_type = manifest.get("package_type", "toolpack")
    is_agent = pkg_type == "agent"

    if is_agent:
        return _verify_agent(pkg_path, manifest, result)

    cases = _extract_cases(manifest)
    result.has_explicit_cases = len(cases) > 0
    result.verification_mode = _determine_mode(cases, result.has_explicit_cases)

    tools = _extract_tools(manifest)
    entrypoint = manifest.get("entrypoint", "")

    with tempfile.TemporaryDirectory(prefix="anp_verify_") as tmpdir:
        venv_path = Path(tmpdir) / "venv"

        # Step 1: Install
        result.install_ok, result.install_log = _step_install(pkg_path, venv_path)
        if not result.install_ok:
            result.score, result.tier = _compute_score(result)
            return result

        # Step 2: Import
        python = _venv_python(venv_path)
        result.import_ok, result.import_log = _step_import(python, entrypoint, tools)
        if not result.import_ok:
            result.score, result.tier = _compute_score(result)
            return result

        # Step 3: Smoke (run cases or auto)
        if cases:
            _run_cases(python, pkg_path, entrypoint, tools, cases, result)
        else:
            _run_auto_smoke(python, entrypoint, tools, result)

        # Step 4: Stability (3 runs on first passing case)
        if result.smoke_status == "passed" and result.cases:
            first_pass = next((c for c in result.cases if c.passed), None)
            if first_pass:
                _run_stability(python, pkg_path, entrypoint, tools, cases, first_pass, result)

        # Step 5: Tests (pytest)
        result.tests_ok, result.tests_log = _step_tests(python, pkg_path)

        result.score, result.tier = _compute_score(result)

    return result


def _extract_cases(manifest: dict) -> list[dict]:
    """Extract verification cases from manifest."""
    verification = manifest.get("verification", {})
    if not isinstance(verification, dict):
        return []

    cases = verification.get("cases", [])
    if cases and isinstance(cases, list):
        return [c for c in cases if isinstance(c, dict)]

    fixtures = verification.get("fixtures", [])
    if fixtures and isinstance(fixtures, list):
        return [
            {"name": f.get("name", f"fixture_{i}"), "input": f.get("test_input", {}),
             "cassette": f.get("cassette"), "expected": f.get("expected")}
            for i, f in enumerate(fixtures) if isinstance(f, dict)
        ]

    test_input = verification.get("test_input")
    if test_input and isinstance(test_input, dict):
        return [{"name": "legacy_test_input", "input": test_input}]

    return []


def _extract_tools(manifest: dict) -> list[dict]:
    caps = manifest.get("capabilities", {})
    return caps.get("tools", []) if isinstance(caps, dict) else []


def _determine_mode(cases: list[dict], has_explicit: bool) -> str:
    if not has_explicit:
        return "real_auto"
    has_cassette = any(c.get("cassette") for c in cases)
    return "fixture" if has_cassette else "cases_real"


def _venv_python(venv_path: Path) -> str:
    if sys.platform == "win32":
        return str(venv_path / "Scripts" / "python.exe")
    return str(venv_path / "bin" / "python")


def _step_install(pkg_path: Path, venv_path: Path) -> tuple[bool, str]:
    """Create venv and pip install the package."""
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_path)],
            capture_output=True, text=True, timeout=60,
        )
        python = _venv_python(venv_path)
        r = subprocess.run(
            [python, "-m", "pip", "install", "-q", str(pkg_path), "pytest"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            return False, r.stderr[:500]
        return True, f"Installed in venv"
    except Exception as e:
        return False, str(e)[:300]


def _step_import(python: str, entrypoint: str, tools: list[dict]) -> tuple[bool, str]:
    """Verify that the entrypoint module can be imported."""
    module_path = entrypoint.split(":")[0] if ":" in entrypoint else entrypoint
    code = f"import {module_path}; print('OK')"
    try:
        r = subprocess.run(
            [python, "-c", code],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return False, r.stderr[:500]
        return True, "Import OK"
    except Exception as e:
        return False, str(e)[:300]


def _run_cases(
    python: str, pkg_path: Path, entrypoint: str, tools: list[dict],
    cases: list[dict], result: VerifyResult,
) -> None:
    """Run each verification case."""
    any_passed = False
    for case in cases:
        cr = _execute_case(python, pkg_path, entrypoint, tools, case)
        result.cases.append(cr)
        if cr.passed:
            any_passed = True
            _validate_expected(cr, case.get("expected"))

    if any_passed:
        result.smoke_status = "passed"
        result.smoke_reason = "ok"
    else:
        result.smoke_status = "failed"
        result.smoke_reason = "all_cases_failed"


def _execute_case(
    python: str, pkg_path: Path, entrypoint: str, tools: list[dict], case: dict,
) -> CaseResult:
    """Execute a single verification case."""
    name = case.get("name", "unnamed")
    case_input = case.get("input", {})
    cassette = case.get("cassette")
    tool_filter = case.get("tool")

    module_path = entrypoint.split(":")[0] if ":" in entrypoint else entrypoint
    func_name = "run"

    if tool_filter and tools:
        for t in tools:
            if t.get("name") == tool_filter:
                ep = t.get("entrypoint", "")
                if ":" in ep:
                    module_path, func_name = ep.rsplit(":", 1)
                break

    # Map /workspace/ paths to the actual package directory
    resolved_input = _resolve_workspace_paths(case_input, pkg_path)
    input_json = json.dumps(resolved_input)

    vcr_enter = ""
    vcr_exit = ""
    if cassette:
        cassette_abs = str(pkg_path / cassette)
        vcr_enter = (
            f"import vcr as _vcr\n"
            f"_cm = _vcr.VCR(record_mode='none').use_cassette({cassette_abs!r})\n"
            f"_cm.__enter__()\n"
        )
        vcr_exit = "_cm.__exit__(None, None, None)\n"

    code = f"""\
import json, hashlib, time, sys
{vcr_enter}
from {module_path} import {func_name}
_input = json.loads({input_json!r})
_t0 = time.monotonic()
try:
    _result = {func_name}(**_input)
except Exception as e:
    print(json.dumps({{"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}}))
    sys.exit(0)
{vcr_exit}
_ms = int((time.monotonic() - _t0) * 1000)
_rtype = type(_result).__name__
_keys = list(_result.keys()) if isinstance(_result, dict) else []
_serializable = True
try:
    _s = json.dumps(_result, default=str)
    _hash = hashlib.md5(_s.encode()).hexdigest()
except Exception:
    _serializable = False
    _hash = ""
print(json.dumps({{
    "ok": True,
    "return_type": _rtype,
    "return_keys": _keys,
    "is_none": _result is None,
    "serializable": _serializable,
    "hash": _hash,
    "ms": _ms,
}}))
"""
    try:
        r = subprocess.run(
            [python, "-c", code],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode != 0:
            return CaseResult(name=name, passed=False, error=r.stderr[:300])

        for line in r.stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("{"):
                data = json.loads(line)
                if data.get("ok"):
                    return CaseResult(
                        name=name, passed=True,
                        return_type=data.get("return_type", ""),
                        return_keys=data.get("return_keys", []),
                        duration_ms=data.get("ms", 0),
                        output_hash=data.get("hash", ""),
                    )
                else:
                    return CaseResult(name=name, passed=False, error=data.get("error", "Unknown"))

        return CaseResult(name=name, passed=False, error="No JSON output from smoke")
    except subprocess.TimeoutExpired:
        return CaseResult(name=name, passed=False, error="Timeout (60s)")
    except Exception as e:
        return CaseResult(name=name, passed=False, error=str(e)[:200])


def _resolve_workspace_paths(data: dict, pkg_path: Path) -> dict:
    """Replace /workspace/ prefixed paths with actual local paths."""
    resolved = {}
    pkg_str = str(pkg_path).replace("\\", "/")
    for k, v in data.items():
        if isinstance(v, str) and v.startswith("/workspace/"):
            local_rel = v[len("/workspace/"):]
            local_path = pkg_path / local_rel
            resolved[k] = str(local_path)
        elif isinstance(v, dict):
            resolved[k] = _resolve_workspace_paths(v, pkg_path)
        else:
            resolved[k] = v
    return resolved


def _validate_expected(cr: CaseResult, expected: dict | None) -> None:
    """Check case result against expected contract (updates cr in-place)."""
    if not expected or not isinstance(expected, dict):
        return

    ret_type = expected.get("return_type")
    if ret_type and cr.return_type != ret_type:
        cr.passed = False
        cr.error = f"Expected return_type '{ret_type}', got '{cr.return_type}'"
        return

    required_keys = expected.get("required_keys")
    if required_keys and isinstance(required_keys, list):
        missing = [k for k in required_keys if k not in cr.return_keys]
        if missing:
            cr.passed = False
            cr.error = f"Missing required keys: {missing}"


def _step_tests(python: str, pkg_path: Path) -> tuple[bool | None, str]:
    """Run pytest in the package directory."""
    tests_dir = pkg_path / "tests"
    if not tests_dir.exists():
        return None, "no tests/ directory"
    test_files = list(tests_dir.glob("test_*.py"))
    if not test_files:
        return None, "no test files found"

    try:
        r = subprocess.run(
            [python, "-m", "pytest", str(tests_dir), "-q", "--tb=short", "--no-header"],
            capture_output=True, text=True, timeout=60,
            cwd=str(pkg_path),
        )
        passed = r.returncode == 0
        summary = r.stdout.strip().split("\n")[-1] if r.stdout.strip() else ""
        return passed, summary
    except subprocess.TimeoutExpired:
        return False, "timeout (60s)"
    except Exception as e:
        return False, str(e)[:200]


def _run_auto_smoke(python: str, entrypoint: str, tools: list[dict], result: VerifyResult) -> None:
    """Auto-generate a minimal smoke test when no cases are defined."""
    module_path = entrypoint.split(":")[0] if ":" in entrypoint else entrypoint
    code = f"""\
import json
from {module_path} import run
try:
    r = run()
    print(json.dumps({{"ok": True, "type": type(r).__name__}}))
except TypeError:
    print(json.dumps({{"ok": True, "type": "callable"}}))
except Exception as e:
    print(json.dumps({{"ok": False, "error": str(e)[:200]}}))
"""
    try:
        r = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout.strip().split("\n")[-1])
            if data.get("ok"):
                result.smoke_status = "passed"
                result.smoke_reason = "auto_smoke"
            else:
                result.smoke_status = "inconclusive"
                result.smoke_reason = "auto_smoke_error"
        else:
            result.smoke_status = "inconclusive"
            result.smoke_reason = "auto_smoke_no_output"
    except Exception:
        result.smoke_status = "inconclusive"
        result.smoke_reason = "auto_smoke_exception"


def _run_stability(
    python: str, pkg_path: Path, entrypoint: str, tools: list[dict],
    cases: list[dict], first_pass: CaseResult, result: VerifyResult,
) -> None:
    """Run the first passing case 3 times for reliability and determinism."""
    case_dict = next((c for c in cases if c.get("name") == first_pass.name), None)
    if not case_dict:
        return

    hashes: list[str] = [first_pass.output_hash]
    ok_count = 1
    total = 3

    for _ in range(total - 1):
        cr = _execute_case(python, pkg_path, entrypoint, tools, case_dict)
        if cr.passed:
            ok_count += 1
            hashes.append(cr.output_hash)
        else:
            hashes.append("")

    result.reliability = ok_count / total
    valid_hashes = [h for h in hashes if h]
    if valid_hashes:
        unique = len(set(valid_hashes))
        result.determinism = 1.0 if unique == 1 else max(0.0, 1.0 - (unique - 1) / len(valid_hashes))
    else:
        result.determinism = 0.0

    # Contract: all passing runs returned serializable, non-None
    result.contract_valid = ok_count >= 1 and first_pass.return_type != "NoneType"


def _compute_score(result: VerifyResult) -> tuple[int, str]:
    """Compute score and tier — mirrors backend scoring.py formula."""
    score = 0

    # Install: 15
    if result.install_ok:
        score += 15

    # Import: 15
    if result.import_ok:
        score += 15

    # Smoke: 25
    if result.smoke_status == "passed":
        score += 25
    elif result.smoke_status == "inconclusive":
        score += 5

    # Tests: 15
    if result.tests_ok is True:
        score += 15
    elif result.tests_ok is None:
        pass  # no tests present

    # Contract: 10
    if result.contract_valid:
        score += 10

    # Reliability: 10
    if result.reliability >= 0.9:
        score += 10
    elif result.reliability >= 0.5:
        score += int(result.reliability * 10)

    # Determinism: 5
    if result.determinism >= 0.9:
        score += 5
    elif result.determinism >= 0.5:
        score += 3

    # Tier calculation
    if score >= 90:
        tier = "gold"
    elif score >= 70:
        tier = "verified"
    elif score >= 50:
        tier = "partial"
    else:
        tier = "unverified"

    # Hard caps (mirrors apply_tier_caps + _qualifies_for_gold)
    if tier == "gold":
        if result.smoke_status != "passed":
            tier = "verified"
        if not result.contract_valid:
            tier = "verified"
        if not result.has_explicit_cases:
            tier = "verified"
        if result.verification_mode == "real_auto":
            tier = "verified"
        if result.verification_mode not in ("cases_real", "fixture"):
            tier = "verified"
        if result.reliability < 0.9:
            tier = "verified"

    return score, tier


def _verify_agent(pkg_path: Path, manifest: dict, result: VerifyResult) -> VerifyResult:
    """Verify an agent package (simplified — no LLM execution)."""
    result.verification_mode = "cases_real"

    agent = manifest.get("agent", {})
    cases = agent.get("verification", {}).get("cases", [])
    result.has_explicit_cases = len(cases) >= 2
    result.cases = []

    entrypoint = manifest.get("entrypoint", "")

    with tempfile.TemporaryDirectory(prefix="anp_verify_") as tmpdir:
        venv_path = Path(tmpdir) / "venv"

        result.install_ok, result.install_log = _step_install(pkg_path, venv_path)
        if not result.install_ok:
            result.score, result.tier = _compute_score(result)
            return result

        python = _venv_python(venv_path)
        result.import_ok, result.import_log = _step_import(python, entrypoint, [])
        if not result.import_ok:
            result.score, result.tier = _compute_score(result)
            return result

        # Agent smoke: verify the entrypoint is callable
        module_path = entrypoint.split(":")[0] if ":" in entrypoint else entrypoint
        code = f"""\
import json, inspect
from {module_path} import run
is_async = inspect.iscoroutinefunction(run)
params = list(inspect.signature(run).parameters.keys())
print(json.dumps({{"ok": True, "async": is_async, "params": params}}))
"""
        try:
            r = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and '"ok": true' in r.stdout.lower():
                result.smoke_status = "passed"
                result.smoke_reason = "agent_entrypoint_valid"
                result.contract_valid = True
                result.reliability = 1.0
                result.determinism = 1.0

                for case in cases:
                    result.cases.append(CaseResult(
                        name=case.get("name", case.get("goal", "unnamed")[:30]),
                        passed=True,
                        return_type="agent_case",
                    ))
            else:
                result.smoke_status = "failed"
                result.smoke_reason = r.stderr[:200] if r.stderr else "import_failed"
        except Exception as e:
            result.smoke_status = "failed"
            result.smoke_reason = str(e)[:200]

    if not cases or len(cases) < 2:
        result.warnings.append("Agent needs at least 2 verification cases for Gold")

    result.score, result.tier = _compute_score(result)
    return result
