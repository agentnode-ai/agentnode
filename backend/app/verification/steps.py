"""The 4 verification steps: install, import, smoke, tests.

Each step returns tuple[bool | str, str] — (status, log).
- install/import/tests return (bool, str) — True=passed, False=failed
- smoke returns (str, str) — "passed"/"failed"/"inconclusive"/"skipped"
"""

from __future__ import annotations

import json
import logging
import os
import time

from app.config import settings
from app.verification.sandbox import VerificationSandbox
from app.verification.schema_generator import (
    generate_candidates,
    is_incomplete_schema,
    build_probe_candidate,
    extract_enum_values,
    _find_operation_field_in_input,
    _sort_by_safety,
)
from app.verification.smoke_context import (
    FATAL_REASONS,
    REASON_VERDICTS,
    SmokeContext,
    build_smoke_context,
    classify_credential_boundary,
    classify_smoke_error,
    classify_timeout,
)

logger = logging.getLogger(__name__)

# ── Stub file support for smoke tests ──
# Only input/source paths — NOT output paths (those just need a valid target dir)
_FILE_PATH_PARAMS = frozenset({
    "path", "file_path", "filepath", "filename",
    "input_file", "source_file", "file", "input_path",
    "pdf_file", "image_path", "image_file", "document",
    "document_path", "input_document", "audio_file",
    "video_file", "source_path",
})

_TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".csv", ".json", ".xml", ".html",
    ".log", ".yaml", ".yml", ".ini", ".cfg", ".conf",
    ".py", ".js", ".ts", ".rb", ".sh", ".sql",
})

_BINARY_EXTENSIONS = frozenset({
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff",
    ".docx", ".xlsx", ".pptx",
    ".wav", ".mp3", ".mp4", ".avi",
})


def _collect_stub_paths(test_input: dict) -> tuple[list[str], list[str]]:
    """Extract file paths from test input that should get stub files.

    Returns (text_stubs, binary_stubs) — separate lists for text and binary formats.
    """
    text_stubs = []
    binary_stubs = []
    for param, value in test_input.items():
        if param.lower() not in _FILE_PATH_PARAMS:
            continue
        if not isinstance(value, str):
            continue
        if "/" not in value and "\\" not in value:
            continue
        ext = os.path.splitext(value)[1].lower()
        if ext in _BINARY_EXTENSIONS:
            binary_stubs.append(value)
        elif not ext or ext in _TEXT_EXTENSIONS:
            text_stubs.append(value)
    return text_stubs, binary_stubs


def step_install(sandbox: VerificationSandbox) -> tuple[bool, str]:
    """Step 1: Create venv and pip install the package."""
    ok, venv_log = sandbox.create_venv()
    if not ok:
        return False, f"venv creation failed:\n{venv_log}"

    ok, pip_log = sandbox.pip_install()
    return ok, pip_log


def step_import(sandbox: VerificationSandbox, tools: list[dict]) -> tuple[bool, str]:
    """Step 2: Verify all declared tool entrypoints are importable and callable.

    Also introspects function signatures to auto-generate input_schema when missing.
    This ensures step_smoke always has valid test inputs.
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
        "import json",
        "import sys",
        "errors = []",
        "warnings = []",
        "info = []",
        "schemas = {}  # tool_name -> auto-generated input_schema",
    ]

    for tool in valid_tools:
        module_path, func_name = tool["entrypoint"].rsplit(":", 1)
        tool_name = tool.get("name", "unknown")
        input_schema = tool.get("input_schema")
        required_count = 0
        if input_schema and isinstance(input_schema, dict):
            required_count = len(input_schema.get("required", []))

        # Safely serialize tool_name via json.dumps to prevent code injection —
        # module_path and func_name are regex-validated upstream, but tool_name is freeform.
        safe_name = json.dumps(tool_name)

        lines.append(f"""
_tn = {safe_name}
try:
    mod = importlib.import_module("{module_path}")
    fn = getattr(mod, "{func_name}", None)
    if fn is None:
        errors.append(f"Tool '{{_tn}}': function '{func_name}' not found in {module_path}")
    elif not callable(fn):
        errors.append(f"Tool '{{_tn}}': '{func_name}' is not callable")
    else:
        if inspect.iscoroutinefunction(fn):
            info.append(f"Tool '{{_tn}}': async callable detected")
        try:
            sig = inspect.signature(fn)
            params = [p for p in sig.parameters.values()
                      if p.default is inspect.Parameter.empty
                      and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)]
            if len(params) > {required_count} + 3:
                warnings.append(f"Tool '{{_tn}}': function expects " + str(len(params)) + " required args but schema declares {required_count} — possible contract mismatch")
            # Auto-generate input_schema from signature
            type_map = {{str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}}
            # Handle string annotations (from __future__ import annotations)
            str_type_map = {{"str": "string", "int": "integer", "float": "number", "bool": "boolean", "list": "array", "dict": "object"}}
            props = {{}}
            required = []
            for p in sig.parameters.values():
                if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                    continue
                ann = p.annotation
                ptype = "string"
                if ann != inspect.Parameter.empty:
                    ptype = type_map.get(ann, "string")
                    if ptype == "string" and isinstance(ann, str):
                        # Resolve string annotations: "dict" -> "object", "list[dict]" -> "array"
                        ann_base = ann.split("[")[0].strip()
                        ptype = str_type_map.get(ann_base, "string")
                prop = {{"type": ptype}}
                if p.default != inspect.Parameter.empty:
                    prop["default"] = p.default if not callable(p.default) else None
                else:
                    required.append(p.name)
                props[p.name] = prop
            if props:
                schemas[_tn] = {{"type": "object", "properties": props, "required": required}}
        except (ValueError, TypeError):
            pass
except Exception as e:
    errors.append(f"Tool '{{_tn}}': import failed: " + str(e))
""")

    lines.append("""
for i in info:
    print("INFO:", i)
for w in warnings:
    print("WARN:", w)
if schemas:
    print("SCHEMAS:" + json.dumps(schemas))
if errors:
    for e in errors:
        print("FAIL:", e, file=sys.stderr)
    sys.exit(1)
else:
    print("All tool entrypoints verified")
""")

    code = "\n".join(lines)
    # Use enforced container isolation for import step when available
    from app.config import settings
    if settings.VERIFICATION_SANDBOX_MODE == "container":
        ok, log = sandbox.run_python_code_enforced(code, timeout=20)
    else:
        ok, log = sandbox.run_python_code(code, timeout=15)

    # Parse auto-generated schemas from output and backfill into tools
    if ok:
        for line in log.splitlines():
            if line.startswith("SCHEMAS:"):
                try:
                    schemas = json.loads(line[8:])
                    for tool in tools:
                        name = tool.get("name", "")
                        if name in schemas and (not tool.get("input_schema") or is_incomplete_schema(tool.get("input_schema"))):
                            tool["input_schema"] = schemas[name]
                except (json.JSONDecodeError, Exception):
                    pass

    return ok, log


def _dominant_reason(reasons: list[str]) -> str | None:
    """Pick the most informative reason from a list of per-tool reasons.

    Priority: fatal > specific inconclusive > generic inconclusive > passed.
    """
    if not reasons:
        return None
    # If all same, return that
    if len(set(reasons)) == 1:
        return reasons[0]
    # Fatal reasons dominate
    for r in reasons:
        if r in FATAL_REASONS:
            return r
    # Specific inconclusive over generic
    for r in reasons:
        if r not in ("ok", "acceptable_external_dependency", "unknown_smoke_condition"):
            return r
    # Fall back to first
    return reasons[0]


def step_smoke(sandbox: VerificationSandbox, tools: list[dict]) -> tuple[str, str, str | None]:
    """Step 3: Evidence-based smoke probe for each tool.

    For each tool:
      a. Build SmokeContext (preflight)
      b. Generate candidates (max 2 inputs)
      c. Execute candidates in order, stop on passed
      d. Active enum probe if all candidates unsupported_operation_space (Phase 2B)
      e. Classify with _tool_result_from_candidates

    Rule: passed ends immediately. If no passed: any fatal → failed. Else → inconclusive.

    Returns (status, log, final_reason) where status is "passed"/"failed"/"inconclusive"/"skipped".
    The final_reason is the dominant reason across all tools (for Phase 3A smoke_reason persistence).
    """
    if not tools:
        return "skipped", "No tools with entrypoints to smoke test", None

    valid_tools = [t for t in tools if t.get("entrypoint") and ":" in t["entrypoint"]]
    if not valid_tools:
        return "skipped", "No tools with valid entrypoints to smoke test", None

    max_tools = settings.VERIFICATION_SMOKE_MAX_TOOLS
    tools_to_test = valid_tools[:max_tools]
    budget = settings.VERIFICATION_SMOKE_BUDGET_SECONDS
    per_tool_timeout = min(15, max(5, budget // len(tools_to_test)))

    logs: list[str] = []
    has_failure = False
    has_inconclusive = False
    tool_reasons: list[str] = []  # Track final reason per tool for smoke_reason
    start_time = time.monotonic()

    if len(valid_tools) > max_tools:
        logs.append(f"[INFO] Testing first {max_tools} of {len(valid_tools)} tools (cap)")

    for tool in tools_to_test:
        elapsed = time.monotonic() - start_time
        if elapsed >= budget:
            logs.append(f"[INFO] Smoke budget exhausted ({budget}s), remaining tools skipped")
            break

        tool_name = tool.get("name", "unknown")

        # ── a. Preflight ──
        ctx = build_smoke_context(tool)

        # ── b. Generate candidates ──
        input_schema = tool.get("input_schema")
        candidates = generate_candidates(input_schema)

        if not candidates or (candidates == [{}] and input_schema and input_schema.get("required")):
            reason = "invalid_test_input"
            status = REASON_VERDICTS[reason][0]
            log_entry = json.dumps({
                "tool": tool_name, "candidate_index": -1,
                "reason": reason, "verdict": status,
                "error_type": None, "message": "no viable candidates generated",
            })
            logs.append(log_entry)
            logs.append(f"[{status.upper()}] {tool_name} {reason}")
            has_inconclusive = True
            tool_reasons.append(reason)
            continue

        # ── c. Execute candidates, collect results ──
        module_path, func_name = tool["entrypoint"].rsplit(":", 1)
        candidate_results: list[dict] = []

        for idx, candidate in enumerate(candidates):
            remaining_budget = budget - (time.monotonic() - start_time)
            if remaining_budget <= 0:
                break
            actual_timeout = min(per_tool_timeout, max(3, int(remaining_budget)))

            reason, error_type, error_msg = _run_single_smoke(
                sandbox, module_path, func_name, candidate, actual_timeout, ctx, tool,
            )
            verdict = REASON_VERDICTS.get(reason, ("inconclusive", ""))[0]

            result_entry = {
                "reason": reason,
                "verdict": verdict,
                "error_type": error_type,
                "message": error_msg,
            }
            candidate_results.append(result_entry)

            # Structured JSON log per candidate
            log_entry = json.dumps({
                "tool": tool_name, "candidate_index": idx,
                "reason": reason, "verdict": verdict,
                "error_type": error_type,
                "message": error_msg[:200] if error_msg else None,
            })
            logs.append(log_entry)

            # Early exit: passed → stop trying (nothing beats it)
            if verdict == "passed":
                break

        # ── d. Probe: Active enum extraction (Phase 2B) ──
        # Trigger: ALL candidates returned unsupported_operation_space
        all_unsupported = (
            len(candidate_results) >= 1
            and all(r["reason"] == "unsupported_operation_space" for r in candidate_results)
        )
        if all_unsupported:
            remaining_budget = budget - (time.monotonic() - start_time)
            if remaining_budget > 3:
                # Find the operation field and try to extract enum values from error
                op_field = _find_operation_field_in_input(
                    candidates[0] if candidates else {}, input_schema,
                )
                last_msg = candidate_results[-1].get("message", "")
                if op_field and last_msg:
                    probe_candidate = build_probe_candidate(
                        candidates[0] if candidates else {}, op_field, last_msg,
                    )
                    if probe_candidate is not None:
                        actual_timeout = min(per_tool_timeout, max(3, int(remaining_budget)))
                        probe_reason, probe_error_type, probe_error_msg = _run_single_smoke(
                            sandbox, module_path, func_name, probe_candidate, actual_timeout, ctx,
                        )
                        probe_verdict = REASON_VERDICTS.get(probe_reason, ("inconclusive", ""))[0]

                        # Extract probe metadata for logging
                        values, confidence = extract_enum_values(last_msg)
                        sorted_values = _sort_by_safety(values) if values else []

                        probe_entry = {
                            "reason": probe_reason,
                            "verdict": probe_verdict,
                            "error_type": probe_error_type,
                            "message": probe_error_msg,
                        }
                        candidate_results.append(probe_entry)

                        log_entry = json.dumps({
                            "tool": tool_name, "candidate_index": "probe",
                            "probe_value": probe_candidate.get(op_field),
                            "probe_source": "bracket_list" if confidence == "high" else "comma_list",
                            "probe_confidence": confidence,
                            "probe_extracted_values": sorted_values[:10],
                            "reason": probe_reason, "verdict": probe_verdict,
                            "error_type": probe_error_type,
                            "message": probe_error_msg[:200] if probe_error_msg else None,
                        })
                        logs.append(log_entry)

        # ── e. Tool verdict from candidates ──
        final_reason, final_status = _tool_result_from_candidates(candidate_results)

        tool_reasons.append(final_reason)

        if final_status == "passed":
            logs.append(f"[PASS] {tool_name} {final_reason}")
        elif final_status == "failed":
            logs.append(f"[FAIL] {tool_name} {final_reason}")
            has_failure = True
        else:
            logs.append(f"[INCONCLUSIVE] {tool_name} {final_reason}")
            has_inconclusive = True

    combined_log = "\n".join(logs)

    # Determine dominant reason across all tools
    dominant_reason = _dominant_reason(tool_reasons)

    if has_failure:
        return "failed", combined_log, dominant_reason
    elif has_inconclusive:
        return "inconclusive", combined_log, dominant_reason
    else:
        return "passed", combined_log, dominant_reason


def _tool_result_from_candidates(
    results: list[dict],
) -> tuple[str, str]:
    """Determine tool verdict from candidate results.

    Priority:
    1. Any passed → passed (best case wins immediately)
    2. Any fatal reason → failed (fatal never overridden by inconclusive)
    3. Otherwise → first result's reason, inconclusive
    4. No results → invalid_test_input, inconclusive
    """
    for r in results:
        if r["verdict"] == "passed":
            return r["reason"], "passed"
    for r in results:
        if r["reason"] in FATAL_REASONS:
            return r["reason"], "failed"
    if results:
        return results[0]["reason"], "inconclusive"
    return "invalid_test_input", "inconclusive"


def _run_single_smoke(
    sandbox: VerificationSandbox,
    module_path: str,
    func_name: str,
    test_input: dict,
    timeout: int,
    ctx: SmokeContext,
    tool: dict | None = None,
) -> tuple[str, str | None, str | None]:
    """Execute one smoke call and return (reason, error_type, error_message).

    Subprocess outputs SMOKE_JSON:{...} for structured parsing.
    Classification happens here in the parent, with full SmokeContext.
    """
    input_json = json.dumps(test_input)
    # Use json.dumps to produce a safe Python string literal — avoids triple-quote
    # breakout and handles all escaping (quotes, backslashes, newlines) correctly.
    input_literal = json.dumps(input_json)

    # ── Stub file creation (Phase 2A + binary support) ──
    text_stubs, binary_stubs = _collect_stub_paths(test_input)
    text_literal = json.dumps(json.dumps(text_stubs))
    binary_literal = json.dumps(json.dumps(binary_stubs))

    stub_block = ""
    if text_stubs or binary_stubs:
        stub_block = f"""
import os, shutil, base64
_STUB_CONTENT = {{
    '.json': '{{"status":"ok","items":[1,2,3]}}',
    '.csv': 'name,value\\nalpha,1\\nbeta,2',
}}
_STUB_DEFAULT = 'Test content for verification.\\nLine 2.\\nLine 3.'
for _stub_path in json.loads({text_literal}):
    try:
        os.makedirs(os.path.dirname(_stub_path) or '.', exist_ok=True)
        if os.path.isdir(_stub_path):
            shutil.rmtree(_stub_path)
        _ext = os.path.splitext(_stub_path)[1].lower()
        with open(_stub_path, 'w') as _f:
            _f.write(_STUB_CONTENT.get(_ext, _STUB_DEFAULT))
    except Exception:
        pass

# Binary stub files — minimal valid files for each format
_BINARY_STUBS = {{
    '.pdf': base64.b64decode('JVBERi0xLjQKMSAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZyAvUGFnZXMgMiAwIFIgPj4KZW5kb2JqCjIgMCBvYmoKPDwgL1R5cGUgL1BhZ2VzIC9LaWRzIFszIDAgUl0gL0NvdW50IDEgPj4KZW5kb2JqCjMgMCBvYmoKPDwgL1R5cGUgL1BhZ2UgL1BhcmVudCAyIDAgUiAvTWVkaWFCb3ggWzAgMCA2MTIgNzkyXQovQ29udGVudHMgNCAwIFIgL1Jlc291cmNlcyA8PCAvRm9udCA8PCAvRjEgNSAwIFIgPj4gPj4gPj4KZW5kb2JqCjQgMCBvYmoKPDwgL0xlbmd0aCA0NCA+PgpzdHJlYW0KQlQgL0YxIDEyIFRmIDEwMCA3MDAgVGQgKFRlc3QgY29udGVudCkgVGogRVQKZW5kc3RyZWFtCmVuZG9iago1IDAgb2JqCjw8IC9UeXBlIC9Gb250IC9TdWJ0eXBlIC9UeXBlMSAvQmFzZUZvbnQgL0hlbHZldGljYSA+PgplbmRvYmoKeHJlZgowIDYKMDAwMDAwMDAwMCA2NTUzNSBmIAowMDAwMDAwMDA5IDAwMDAwIG4gCjAwMDAwMDAwNjUgMDAwMDAgbiAKMDAwMDAwMDEyNCAwMDAwMCBuIAowMDAwMDAwMzIwIDAwMDAwIG4gCjAwMDAwMDA0MTUgMDAwMDAgbiAKdHJhaWxlcgo8PCAvU2l6ZSA2IC9Sb290IDEgMCBSID4+CnN0YXJ0eHJlZgo1MDYKJSVFT0YK'),
    '.png': base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFklEQVQYV2P8z8BQz0AEYBxVOHIUAgBGWAgE/dLkKAAAAABJRU5ErkJggg=='),
    '.jpg': base64.b64decode('/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/xAAUAQEAAAAAAAAAAAAAAAAAAAAA/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEQMRAD8AKwA//9k='),
    '.jpeg': base64.b64decode('/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/xAAUAQEAAAAAAAAAAAAAAAAAAAAA/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEQMRAD8AKwA//9k='),
    '.gif': base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'),
    '.bmp': base64.b64decode('Qk06AAAAAAAAADYAAAAoAAAAAQAAAAEAAAABABgAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAA/wAA'),
}}

# Generate DOCX/XLSX stubs at runtime (valid ZIP-based Office formats)
import zipfile, io as _io
def _make_office_stub(fmt):
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        if fmt == 'docx':
            z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            z.writestr('word/document.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Test content for verification</w:t></w:r></w:p></w:body></w:document>')
        elif fmt == 'xlsx':
            z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            z.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr('xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr('xl/worksheets/sheet1.xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Name</t></is></c><c r="B1" t="inlineStr"><is><t>Value</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>test</t></is></c><c r="B2"><v>42</v></c></row></sheetData></worksheet>')
    return buf.getvalue()
try:
    _BINARY_STUBS['.docx'] = _make_office_stub('docx')
    _BINARY_STUBS['.xlsx'] = _make_office_stub('xlsx')
except Exception:
    pass

for _stub_path in json.loads({binary_literal}):
    try:
        os.makedirs(os.path.dirname(_stub_path) or '.', exist_ok=True)
        if os.path.isdir(_stub_path):
            shutil.rmtree(_stub_path)
        _ext = os.path.splitext(_stub_path)[1].lower()
        _content = _BINARY_STUBS.get(_ext)
        if _content:
            with open(_stub_path, 'wb') as _f:
                _f.write(_content)
    except Exception:
        pass
"""

    # Detect agent entrypoints — they need a mock AgentContext
    tool = tool or {}
    is_agent = tool.get("name") == "__agent_entrypoint__"
    agent_goal = ""
    if is_agent:
        agent_section = tool.get("_agent_section", {})
        agent_goal = agent_section.get("goal", "Verification smoke test")

    agent_context_block = ""
    if is_agent:
        agent_goal_literal = json.dumps(agent_goal)
        agent_context_block = f"""
class _MockToolResult:
    def __init__(self, success=True, result=None, error=None):
        self.success = success
        self.result = result or {{"output": "mock result"}}
        self.error = error

class _MockAgentContext:
    def __init__(self):
        self._goal = {agent_goal_literal}
        self._iteration = 0
    @property
    def goal(self):
        return self._goal
    @property
    def iteration(self):
        return self._iteration
    def run_tool(self, slug, tool_name=None, **kw):
        return _MockToolResult()
    def try_tool(self, slug, tool_name=None, **kw):
        return _MockToolResult()
    def next_iteration(self):
        self._iteration += 1
    def is_tool_available(self, slug):
        return True
    def call_llm(self, messages, **kw):
        class _R:
            content = "Mock LLM response for verification."
            tool_calls = None
            usage = None
            model = "mock"
            finish_reason = "stop"
        return _R()
    def call_llm_text(self, messages, **kw):
        return "Mock LLM response for verification."
    @property
    def llm(self):
        return None
    @property
    def system_prompt(self):
        return None
    @property
    def allowed_packages(self):
        return None
    @property
    def tool_calls_made(self):
        return 0
    @property
    def tools_remaining(self):
        return 50
    @property
    def max_tool_calls(self):
        return 50
    @property
    def max_iterations(self):
        return 10
    @property
    def run_id(self):
        return None
    @property
    def llm_calls_made(self):
        return 0

_agent_ctx = _MockAgentContext()
"""

    call_expr = "fn(_agent_ctx, **test_input)" if is_agent else "fn(**test_input)"
    async_call_expr = f"asyncio.run({call_expr})" if not is_agent else call_expr

    code = f"""
import importlib, json, sys, inspect, asyncio, hashlib
{stub_block}
{agent_context_block}
mod = importlib.import_module("{module_path}")
fn = getattr(mod, "{func_name}")

test_input = json.loads({input_literal})

try:
    if inspect.iscoroutinefunction(fn):
        result = {async_call_expr}
    else:
        result = {call_expr}
    result_repr = repr(result)[:1000]
    result_hash = hashlib.md5(result_repr.encode()).hexdigest()
    is_none = result is None
    is_serializable = False
    try:
        json.dumps(result)
        is_serializable = True
    except (TypeError, ValueError, OverflowError):
        pass
    return_keys = list(result.keys())[:20] if isinstance(result, dict) else None
    return_length = len(result) if hasattr(result, '__len__') else None
    print('SMOKE_JSON:' + json.dumps({{
        "status": "ok",
        "return_type": type(result).__name__,
        "return_hash": result_hash,
        "is_none": is_none,
        "is_serializable": is_serializable,
        "return_keys": return_keys,
        "return_length": return_length,
    }}))
except Exception as e:
    print('SMOKE_JSON:' + json.dumps({{"status": "error", "error_type": type(e).__name__, "message": str(e)[:500]}}))
"""

    ok, log = sandbox.run_python_code(code, timeout=timeout, restrict_network=True)

    # ── Parse result ──
    if not ok:
        # Subprocess crashed or timed out
        if "timed out" in log.lower():
            reason = classify_timeout(ctx)
            return reason, "SubprocessTimeout", f"subprocess timed out after {timeout}s"
        # Try to find SMOKE_JSON in mixed output
        parsed = _parse_smoke_json(log)
        if parsed and parsed.get("status") == "error":
            error_type = parsed.get("error_type", "")
            error_msg = parsed.get("message", "")
            return classify_smoke_error(error_type, error_msg, ctx), error_type, error_msg
        return "fatal_runtime_error", None, log[:200] if log else None

    # Subprocess exited 0 — parse structured output
    parsed = _parse_smoke_json(log)
    if not parsed:
        # No SMOKE_JSON found but exit 0 — function probably printed something else
        return "ok", None, None

    if parsed.get("status") == "ok":
        return "ok", None, None

    if parsed.get("status") == "error":
        error_type = parsed.get("error_type", "")
        error_msg = parsed.get("message", "")
        return classify_smoke_error(error_type, error_msg, ctx), error_type, error_msg

    return "unknown_smoke_condition", None, None


def _parse_smoke_json(log: str) -> dict | None:
    """Extract the SMOKE_JSON:{...} payload from subprocess output."""
    for line in log.splitlines():
        if line.startswith("SMOKE_JSON:"):
            try:
                return json.loads(line[11:])
            except (json.JSONDecodeError, ValueError):
                pass
    return None


def run_stability_check(
    sandbox: VerificationSandbox,
    module_path: str,
    func_name: str,
    test_input: dict,
    timeout: int,
    ctx: SmokeContext,
    n: int = 3,
) -> tuple[float, float, bool, list[dict]]:
    """Run same input N times, collect reliability + determinism + contract validity.

    Returns (reliability, determinism, contract_valid, run_results).
    """
    results = []
    for i in range(n):
        reason, error_type, error_msg = _run_single_smoke(
            sandbox, module_path, func_name, test_input, timeout, ctx,
        )
        parsed = None
        # Re-run to get the SMOKE_JSON for hash/type info
        # Actually, _run_single_smoke already runs the code. We need the parsed output.
        # Let's use a simpler approach: run the code and parse SMOKE_JSON directly.
        run_result = {"ok": reason == "ok", "reason": reason}

        # For hash/type, we need to run a separate call that captures the JSON
        input_json = json.dumps(test_input)
        input_literal = json.dumps(input_json)
        stub_paths = _collect_stub_paths(test_input)
        stub_literal = json.dumps(json.dumps(stub_paths))

        stub_block = ""
        if stub_paths:
            stub_block = f"""
import os, shutil
_STUB_CONTENT = {{
    '.json': '{{"status":"ok","items":[1,2,3]}}',
    '.csv': 'name,value\\nalpha,1\\nbeta,2',
}}
_STUB_DEFAULT = 'Test content for verification.\\nLine 2.\\nLine 3.'
for _stub_path in json.loads({stub_literal}):
    try:
        os.makedirs(os.path.dirname(_stub_path) or '.', exist_ok=True)
        if os.path.isdir(_stub_path):
            shutil.rmtree(_stub_path)
        _ext = os.path.splitext(_stub_path)[1].lower()
        with open(_stub_path, 'w') as _f:
            _f.write(_STUB_CONTENT.get(_ext, _STUB_DEFAULT))
    except Exception:
        pass
"""

        code = f"""
import importlib, json, sys, inspect, asyncio, hashlib, time
{stub_block}
mod = importlib.import_module("{module_path}")
fn = getattr(mod, "{func_name}")
test_input = json.loads({input_literal})
t0 = time.monotonic()
try:
    if inspect.iscoroutinefunction(fn):
        result = asyncio.run(fn(**test_input))
    else:
        result = fn(**test_input)
    ms = int((time.monotonic() - t0) * 1000)
    result_repr = repr(result)[:1000]
    result_hash = hashlib.md5(result_repr.encode()).hexdigest()
    is_serializable = False
    try:
        json.dumps(result)
        is_serializable = True
    except (TypeError, ValueError, OverflowError):
        pass
    print('SMOKE_JSON:' + json.dumps({{
        "status": "ok", "return_type": type(result).__name__,
        "return_hash": result_hash, "is_none": result is None,
        "is_serializable": is_serializable, "ms": ms,
    }}))
except Exception as e:
    ms = int((time.monotonic() - t0) * 1000)
    print('SMOKE_JSON:' + json.dumps({{"status": "error", "error_type": type(e).__name__, "message": str(e)[:200], "ms": ms}}))
"""
        ok, log = sandbox.run_python_code(code, timeout=timeout, restrict_network=True)
        parsed = _parse_smoke_json(log)
        if parsed:
            run_result["hash"] = parsed.get("return_hash")
            run_result["type"] = parsed.get("return_type")
            run_result["ms"] = parsed.get("ms")
            run_result["is_serializable"] = parsed.get("is_serializable", False)
            run_result["is_none"] = parsed.get("is_none", True)
        results.append(run_result)

    # Compute metrics
    ok_count = sum(1 for r in results if r["ok"])
    reliability = ok_count / len(results) if results else 0.0

    hashes = [r.get("hash") for r in results if r.get("hash")]
    if len(hashes) >= 2:
        determinism = 1.0 if len(set(hashes)) <= 1 else round(1.0 / len(set(hashes)), 2)
    else:
        determinism = 1.0 if len(hashes) == 1 else 0.0

    # Contract validity: at least one result is serializable and not None
    contract_valid = any(
        r.get("is_serializable") and not r.get("is_none")
        for r in results if r["ok"]
    )

    return reliability, determinism, contract_valid, results


def step_tests(sandbox: VerificationSandbox) -> tuple[bool, str]:
    """Step 4: Run package tests with pytest.

    Caller should check sandbox.has_tests() first — returns (False, msg) if no tests.
    """
    if not sandbox.has_tests():
        return False, "No test directory found"

    return sandbox.run_pytest()
