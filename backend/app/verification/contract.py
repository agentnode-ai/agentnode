"""Contract validation for tool outputs (Phase 6A).

Deterministic checks — no AI. Validates that tool outputs meet basic
contract expectations: non-None, serializable, correct type, structure.

Also includes light semantic sanity checks (never fatal, max -2 points).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def validate_return(smoke_data: dict, tool_name: str, input_data: dict) -> dict:
    """Validate a smoke test return value against contract expectations.

    Args:
        smoke_data: Parsed SMOKE_JSON from the smoke test run.
        tool_name: Name of the tool (for semantic hints).
        input_data: The test input that was used.

    Returns:
        Contract validation result dict with:
        - valid: bool (all Level 1 checks passed)
        - checks: list of individual check results
        - hints: list of semantic sanity hints (Level 2, never fatal)
        - points: suggested contract points (0-10)
        - max_points: 10
        - reason: human-readable summary
    """
    if smoke_data.get("status") != "ok":
        return {
            "valid": False,
            "checks": [],
            "hints": [],
            "points": 0,
            "max_points": 10,
            "reason": "Smoke test did not return ok status",
        }

    checks = []
    all_passed = True

    # ── Level 1: Core contract checks (always run) ──

    # 1. Non-None check
    is_none = smoke_data.get("is_none", True)
    checks.append({
        "name": "non_none",
        "passed": not is_none,
        "detail": "Return value is not None" if not is_none else "Return value is None",
    })
    if is_none:
        all_passed = False

    # 2. Serializable check
    is_serializable = smoke_data.get("is_serializable", False)
    checks.append({
        "name": "serializable",
        "passed": is_serializable,
        "detail": "Output is JSON-serializable" if is_serializable else "Output is not JSON-serializable",
    })
    if not is_serializable:
        all_passed = False

    # 3. Type plausibility (return_type exists and is reasonable)
    return_type = smoke_data.get("return_type")
    type_ok = return_type is not None and return_type != "NoneType"
    checks.append({
        "name": "type_present",
        "passed": type_ok,
        "detail": f"Return type: {return_type}" if type_ok else "No return type detected",
    })
    if not type_ok:
        all_passed = False

    # 4. Structure plausibility
    return_keys = smoke_data.get("return_keys")
    return_length = smoke_data.get("return_length")
    structure_ok = True
    structure_detail = "Structure looks reasonable"

    if return_type == "dict":
        if return_keys is not None and len(return_keys) == 0:
            structure_ok = False
            structure_detail = "Dict output has no keys (empty dict)"
    elif return_type == "list":
        if return_length is not None and return_length == 0:
            # Empty list is suspicious but not necessarily wrong
            structure_detail = "List output is empty (may be expected)"
    elif return_type == "str":
        if return_length is not None and return_length == 0:
            structure_ok = False
            structure_detail = "String output is empty"

    checks.append({
        "name": "structure",
        "passed": structure_ok,
        "detail": structure_detail,
    })
    if not structure_ok:
        all_passed = False

    # ── Level 2: Semantic sanity checks (light, never fatal) ──
    hints = semantic_sanity_check(tool_name, input_data, smoke_data)

    # ── Score calculation ──
    passed_count = sum(1 for c in checks if c["passed"])
    total_checks = len(checks)

    if all_passed and not hints:
        points = 10
        reason = "All contract checks passed"
    elif all_passed and hints:
        points = 8  # Minor sanity concerns
        reason = f"Contract valid, {len(hints)} sanity hint(s)"
    elif passed_count >= 3:
        points = 6
        reason = f"{passed_count}/{total_checks} contract checks passed"
    elif passed_count >= 2:
        points = 4
        reason = f"{passed_count}/{total_checks} contract checks passed"
    else:
        points = 2
        reason = f"Only {passed_count}/{total_checks} contract checks passed"

    # Deduct for hints (max -2)
    hint_deduction = min(len(hints), 2)
    points = max(0, points - hint_deduction)

    return {
        "valid": all_passed,
        "checks": checks,
        "hints": hints,
        "points": points,
        "max_points": 10,
        "reason": reason,
    }


def semantic_sanity_check(
    tool_name: str, input_data: dict, smoke_data: dict,
) -> list[str]:
    """Light heuristic checks based on tool name patterns.

    NEVER fatal. Max small contract deduction.
    """
    hints = []
    name_lower = tool_name.lower()
    return_type = smoke_data.get("return_type", "")
    return_length = smoke_data.get("return_length")

    # JSON/dict tools should return dicts
    if any(kw in name_lower for kw in ("json", "parse", "extract")):
        if return_type not in ("dict", "list"):
            hints.append("Expected dict/list output from extraction/parsing tool")

    # Summary should be shorter than input
    if "summar" in name_lower:
        input_text = input_data.get("text", "")
        if (
            return_type == "str"
            and return_length is not None
            and isinstance(input_text, str)
            and len(input_text) > 50
            and return_length >= len(input_text)
        ):
            hints.append("Summary longer than input text")

    # Convert should return non-empty
    if "convert" in name_lower:
        if return_type == "str" and return_length is not None and return_length == 0:
            hints.append("Converter returned empty output")

    # Search/find should return list or dict
    if any(kw in name_lower for kw in ("search", "find", "query", "list")):
        if return_type == "str":
            hints.append("Search/query tool returned plain string instead of structured data")

    return hints


def validate_format(
    tool_name: str, smoke_data: dict,
) -> dict | None:
    """Format validation for converter tools (Phase 6C).

    Only checks syntactic validity for clearly invertible format pairs.
    Returns None if not applicable.
    """
    name_lower = tool_name.lower()

    # Only apply to tools with format-related names
    if not any(kw in name_lower for kw in ("convert", "to_json", "to_csv", "to_xml", "format")):
        return None

    return_type = smoke_data.get("return_type", "")
    return_keys = smoke_data.get("return_keys")
    return_length = smoke_data.get("return_length")

    result = {"applicable": True, "checks": []}

    # Check if output claiming to be JSON is actually valid JSON structure
    if "json" in name_lower and return_type == "str":
        # String output from a JSON tool — suspicious but could be serialized JSON
        result["checks"].append({
            "name": "json_format",
            "passed": True,  # We can't verify content from metadata alone
            "detail": "Output is string (may be serialized JSON)",
        })

    # Non-empty output check
    if return_length is not None and return_length == 0:
        result["checks"].append({
            "name": "non_empty",
            "passed": False,
            "detail": "Converter produced empty output",
        })
    elif return_length is not None:
        result["checks"].append({
            "name": "non_empty",
            "passed": True,
            "detail": f"Output has length {return_length}",
        })

    return result if result["checks"] else None
