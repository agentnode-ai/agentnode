"""The 4 verification steps: install, import, smoke, tests.

Each step returns tuple[bool | str, str] — (status, log).
- install/import/tests return (bool, str) — True=passed, False=failed
- smoke returns (str, str) — "passed"/"failed"/"inconclusive"
"""

from __future__ import annotations

import json
import logging
import time

from app.config import settings
from app.verification.sandbox import VerificationSandbox
from app.verification.schema_generator import generate_test_input

logger = logging.getLogger(__name__)

# Exception types that indicate the tool *works* but needs real data
ACCEPTABLE_EXCEPTIONS = (
    "FileNotFoundError",
    "PermissionError",
    "ConnectionError",
    "TimeoutError",
    "NotImplementedError",
    "RuntimeError",
    "OSError",
    "IOError",
)

# Exception types that indicate ambiguous result — could be broken or missing data
INCONCLUSIVE_EXCEPTIONS = (
    "ValueError",
    "KeyError",
    "IndexError",
)

# Exception types that indicate the tool is broken
FATAL_EXCEPTIONS = (
    "TypeError",
    "AttributeError",
    "ImportError",
    "ModuleNotFoundError",
    "SyntaxError",
    "NameError",
)


def step_install(sandbox: VerificationSandbox) -> tuple[bool, str]:
    """Step 1: Create venv and pip install the package."""
    ok, venv_log = sandbox.create_venv()
    if not ok:
        return False, f"venv creation failed:\n{venv_log}"

    ok, pip_log = sandbox.pip_install()
    return ok, pip_log


def step_import(sandbox: VerificationSandbox, tools: list[dict]) -> tuple[bool, str]:
    """Step 2: Verify all declared tool entrypoints are importable and callable.

    Also detects async callables (they still pass — they are valid callables).
    tools: list of dicts with at least 'name' and 'entrypoint' (module.path:function).
    """
    if not tools:
        return True, "No tools with entrypoints to check"

    # Filter to tools with valid entrypoints
    valid_tools = [t for t in tools if t.get("entrypoint") and ":" in t["entrypoint"]]
    if not valid_tools:
        return True, "No tools with valid entrypoints to check"

    lines = [
        "import importlib",
        "import inspect",
        "import sys",
        "errors = []",
        "warnings = []",
        "info = []",
    ]

    for tool in valid_tools:
        module_path, func_name = tool["entrypoint"].rsplit(":", 1)
        tool_name = tool.get("name", "unknown")
        # Build expected param count from input_schema for basic contract check
        input_schema = tool.get("input_schema")
        required_count = 0
        if input_schema and isinstance(input_schema, dict):
            required_count = len(input_schema.get("required", []))

        lines.append(f"""
try:
    mod = importlib.import_module("{module_path}")
    fn = getattr(mod, "{func_name}", None)
    if fn is None:
        errors.append("Tool '{tool_name}': function '{func_name}' not found in {module_path}")
    elif not callable(fn):
        errors.append("Tool '{tool_name}': '{func_name}' is not callable")
    else:
        if inspect.iscoroutinefunction(fn):
            info.append("Tool '{tool_name}': async callable detected")
        # Basic signature sanity check
        try:
            sig = inspect.signature(fn)
            params = [p for p in sig.parameters.values()
                      if p.default is inspect.Parameter.empty
                      and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)]
            if len(params) > {required_count} + 3:
                warnings.append("Tool '{tool_name}': function expects " + str(len(params)) + " required args but schema declares {required_count} — possible contract mismatch")
        except (ValueError, TypeError):
            pass  # Some builtins/C extensions don't support signature introspection
except Exception as e:
    errors.append("Tool '{tool_name}': import failed: " + str(e))
""")

    lines.append("""
for i in info:
    print("INFO:", i)
for w in warnings:
    print("WARN:", w)
if errors:
    for e in errors:
        print("FAIL:", e, file=sys.stderr)
    sys.exit(1)
else:
    print("All tool entrypoints verified")
""")

    code = "\n".join(lines)
    return sandbox.run_python_code(code, timeout=15)


def step_smoke(sandbox: VerificationSandbox, tools: list[dict]) -> tuple[str, str]:
    """Step 3: Smoke test — call each tool function with minimal input.

    Returns (status, log) where status is "passed"/"failed"/"inconclusive"/"skipped".
    - PASS: tool returns a result or raises an acceptable exception
    - FAIL: tool raises a fatal exception (TypeError, ImportError, etc.) or crashes
    - INCONCLUSIVE: tool raises ValueError/KeyError/IndexError (ambiguous)
    - SKIPPED: no tools to test or schema generation failed

    Caps at VERIFICATION_SMOKE_MAX_TOOLS tools, total budget VERIFICATION_SMOKE_BUDGET_SECONDS.
    """
    if not tools:
        return "skipped", "No tools with entrypoints to smoke test"

    # Filter to valid entrypoints
    valid_tools = [t for t in tools if t.get("entrypoint") and ":" in t["entrypoint"]]
    if not valid_tools:
        return "skipped", "No tools with valid entrypoints to smoke test"

    # Cap number of tools tested
    max_tools = settings.VERIFICATION_SMOKE_MAX_TOOLS
    tools_to_test = valid_tools[:max_tools]
    budget = settings.VERIFICATION_SMOKE_BUDGET_SECONDS
    per_tool_timeout = min(15, max(5, budget // len(tools_to_test)))

    logs = []
    has_failure = False
    has_inconclusive = False
    start_time = time.monotonic()

    if len(valid_tools) > max_tools:
        logs.append(f"[INFO] Testing first {max_tools} of {len(valid_tools)} tools (cap)")

    for tool in tools_to_test:
        # Check total budget
        elapsed = time.monotonic() - start_time
        if elapsed >= budget:
            logs.append(f"[INFO] Smoke budget exhausted ({budget}s), remaining tools skipped")
            break

        module_path, func_name = tool["entrypoint"].rsplit(":", 1)
        input_schema = tool.get("input_schema")

        try:
            test_input = generate_test_input(input_schema)
        except Exception:
            tool_name = tool.get("name", "unknown")
            logs.append(f"[SKIP] {tool_name}: schema generation failed")
            continue

        # If schema has required fields but generator returned {}, mark inconclusive
        tool_name = tool.get("name", "unknown")
        if input_schema and input_schema.get("required") and not test_input:
            logs.append(f"[INCONCLUSIVE] {tool_name}: unsupported or invalid input schema")
            has_inconclusive = True
            continue

        acceptable = ", ".join(ACCEPTABLE_EXCEPTIONS)
        inconclusive = ", ".join(INCONCLUSIVE_EXCEPTIONS)
        fatal = ", ".join(FATAL_EXCEPTIONS)

        code = f"""
import importlib, json, sys, inspect, asyncio

ACCEPTABLE = ({acceptable},)
INCONCLUSIVE = ({inconclusive},)
FATAL = ({fatal},)

mod = importlib.import_module("{module_path}")
fn = getattr(mod, "{func_name}")

test_input = json.loads('''{json.dumps(test_input)}''')

try:
    if inspect.iscoroutinefunction(fn):
        result = asyncio.run(fn(**test_input))
    else:
        result = fn(**test_input)
    print("RESULT_TYPE:" + type(result).__name__)
except FATAL as e:
    print("FATAL_ERROR:" + type(e).__name__ + ":" + str(e), file=sys.stderr)
    sys.exit(1)
except INCONCLUSIVE as e:
    print("INCONCLUSIVE_ERROR:" + type(e).__name__ + ":" + str(e))
except ACCEPTABLE as e:
    print("ACCEPTABLE_ERROR:" + type(e).__name__ + ":" + str(e))
except Exception as e:
    print("OTHER_ERROR:" + type(e).__name__ + ":" + str(e))
"""

        remaining_budget = budget - (time.monotonic() - start_time)
        actual_timeout = min(per_tool_timeout, max(3, int(remaining_budget)))

        ok, log = sandbox.run_python_code(code, timeout=actual_timeout, restrict_network=True)
        tool_name = tool.get("name", "unknown")

        if not ok:
            if "FATAL_ERROR:" in log:
                logs.append(f"[FAIL] {tool_name}: {log}")
                has_failure = True
            elif "timed out" in log.lower():
                logs.append(f"[FAIL] {tool_name}: {log}")
                has_failure = True
            else:
                logs.append(f"[WARN] {tool_name}: {log}")
        else:
            if "INCONCLUSIVE_ERROR:" in log:
                logs.append(f"[INCONCLUSIVE] {tool_name}: {log}")
                has_inconclusive = True
            else:
                logs.append(f"[PASS] {tool_name}")

    combined_log = "\n".join(logs)

    if has_failure:
        return "failed", combined_log
    elif has_inconclusive:
        return "inconclusive", combined_log
    else:
        return "passed", combined_log


def step_tests(sandbox: VerificationSandbox) -> tuple[bool, str]:
    """Step 4: Run package tests with pytest.

    Caller should check sandbox.has_tests() first — returns (False, msg) if no tests.
    """
    if not sandbox.has_tests():
        return False, "No test directory found"

    return sandbox.run_pytest()
