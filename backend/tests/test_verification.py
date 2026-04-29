"""Tests for the verification pipeline."""

import pytest

from app.verification.schema_generator import (
    generate_test_input,
    generate_candidates,
    is_incomplete_schema,
    _generate_value,
    _find_operation_field,
    _find_operation_field_in_input,
    extract_enum_values,
    _sort_by_safety,
    build_probe_candidate,
    NAME_HINTS,
    OPERATION_CANDIDATES,
)
from app.verification.steps import _collect_stub_paths, _dominant_reason
from app.verification.smoke_context import (
    FATAL_REASONS,
    REASON_VERDICTS,
    SmokeContext,
    build_smoke_context,
    classify_smoke_error,
    classify_timeout,
)


# --- schema_generator tests ---

class TestSchemaGenerator:

    def test_empty_schema(self):
        assert generate_test_input(None) == {}
        assert generate_test_input({}) == {}

    def test_string_type(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        result = generate_test_input(schema)
        assert result == {"name": "test-item"}  # NAME_HINTS match

    def test_integer_type(self):
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        }
        result = generate_test_input(schema)
        assert result == {"count": 5}  # NAME_HINTS match

    def test_number_type(self):
        schema = {
            "type": "object",
            "properties": {"score": {"type": "number"}},
            "required": ["score"],
        }
        result = generate_test_input(schema)
        assert result == {"score": 1.0}

    def test_boolean_type(self):
        schema = {
            "type": "object",
            "properties": {"flag": {"type": "boolean"}},
            "required": ["flag"],
        }
        result = generate_test_input(schema)
        assert result == {"flag": True}

    def test_array_type(self):
        schema = {
            "type": "object",
            "properties": {"items": {"type": "array"}},
            "required": ["items"],
        }
        result = generate_test_input(schema)
        assert result == {"items": []}

    def test_default_value(self):
        schema = {
            "type": "object",
            "properties": {"lang": {"type": "string", "default": "en"}},
            "required": ["lang"],
        }
        result = generate_test_input(schema)
        assert result == {"lang": "en"}

    def test_enum_value(self):
        schema = {
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": ["fast", "slow"]}},
            "required": ["mode"],
        }
        result = generate_test_input(schema)
        assert result == {"mode": "fast"}

    def test_optional_field_skipped(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "optional_field": {"type": "string"},
            },
            "required": ["name"],
        }
        result = generate_test_input(schema)
        assert "name" in result
        assert "optional_field" not in result

    def test_optional_field_with_default_included(self):
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "lang": {"type": "string", "default": "en"},
            },
            "required": ["name"],
        }
        result = generate_test_input(schema)
        assert result["name"] == "test-item"  # NAME_HINTS match
        assert result["lang"] == "en"

    def test_nested_object(self):
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {"timeout": {"type": "integer"}},
                    "required": ["timeout"],
                }
            },
            "required": ["config"],
        }
        result = generate_test_input(schema)
        # Nested object: config is required so gets generated,
        # but timeout inside it is depth 1 object which calls generate_test_input recursively
        assert "config" in result

    def test_no_properties(self):
        schema = {"type": "object"}
        result = generate_test_input(schema)
        assert result == {}

    def test_max_recursion_depth(self):
        """Deeply nested schemas should stop at depth 3."""
        schema = {
            "type": "object",
            "properties": {
                "a": {
                    "type": "object",
                    "properties": {
                        "b": {
                            "type": "object",
                            "properties": {
                                "c": {
                                    "type": "object",
                                    "properties": {
                                        "d": {"type": "string"},
                                    },
                                    "required": ["d"],
                                }
                            },
                            "required": ["c"],
                        }
                    },
                    "required": ["b"],
                }
            },
            "required": ["a"],
        }
        result = generate_test_input(schema)
        # Depth 0: a, depth 1: b, depth 2: c, depth 3: returns {} (max depth)
        assert result == {"a": {"b": {"c": {}}}}

    def test_broken_schema_returns_empty(self):
        """Broken/malformed schemas should return {} instead of crashing."""
        assert generate_test_input({"properties": "not_a_dict"}) == {}
        assert generate_test_input({"properties": {"x": "not_a_dict"}}) == {}

    def test_self_referential_schema_safe(self):
        """Schemas that could cause infinite recursion should be capped."""
        # Not truly self-referential, but deeply nested
        schema = {
            "type": "object",
            "properties": {"inner": {"type": "object"}},
            "required": ["inner"],
        }
        result = generate_test_input(schema)
        assert result == {"inner": {}}


# --- steps tests (unit-level, no subprocess) ---

class TestStepImportCodeGeneration:
    """Test that import step generates valid Python code."""

    def test_import_code_for_tools(self):
        from app.verification.steps import step_import
        from unittest.mock import MagicMock
        sandbox = MagicMock()
        sandbox.run_python_code.return_value = (True, "All tool entrypoints verified")
        tools = [
            {"name": "count_words", "entrypoint": "word_counter.main:count_words"},
            {"name": "analyze", "entrypoint": "analyzer.core:analyze"},
        ]
        ok, log = step_import(sandbox, tools)
        assert ok is True

    def test_import_no_tools(self):
        from app.verification.steps import step_import
        from unittest.mock import MagicMock
        sandbox = MagicMock()
        ok, log = step_import(sandbox, [])
        assert ok is True
        assert "No tools" in log

    def test_import_tools_without_entrypoint(self):
        from app.verification.steps import step_import
        from unittest.mock import MagicMock
        sandbox = MagicMock()
        tools = [{"name": "test", "entrypoint": None}]
        ok, log = step_import(sandbox, tools)
        assert ok is True


class TestStepSmoke:

    def test_smoke_no_tools(self):
        from app.verification.steps import step_smoke
        from unittest.mock import MagicMock
        sandbox = MagicMock()
        status, log, reason, _candidate = step_smoke(sandbox, [])
        assert status == "skipped"

    def test_smoke_returns_string_status(self):
        """Smoke step should return status string, not bool."""
        from app.verification.steps import step_smoke
        from unittest.mock import MagicMock
        sandbox = MagicMock()
        # Return SMOKE_JSON with ok status
        sandbox.run_python_code.return_value = (
            True,
            'SMOKE_JSON:{"status":"ok","return_type":"dict"}',
        )
        tools = [
            {
                "name": "count_words",
                "entrypoint": "word_counter.main:count_words",
                "input_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }
        ]
        status, log, reason, _candidate = step_smoke(sandbox, tools)
        assert status == "passed"
        assert isinstance(status, str)
        assert "[PASS]" in log

    def test_smoke_inconclusive_on_value_error(self):
        """ValueError without input-rejection keywords → unknown_smoke_condition → inconclusive."""
        from app.verification.steps import step_smoke
        from unittest.mock import MagicMock
        sandbox = MagicMock()
        sandbox.run_python_code.return_value = (
            True,
            'SMOKE_JSON:{"status":"error","error_type":"ValueError","message":"bad input"}',
        )
        tools = [
            {
                "name": "tool1",
                "entrypoint": "mod:func",
                "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
            }
        ]
        status, log, reason, _candidate = step_smoke(sandbox, tools)
        assert status == "inconclusive"
        assert "[INCONCLUSIVE]" in log

    def test_smoke_failed_on_fatal_error(self):
        """Fatal errors (subprocess crash) should result in failed."""
        from app.verification.steps import step_smoke
        from unittest.mock import MagicMock
        sandbox = MagicMock()
        sandbox.run_python_code.return_value = (False, "Traceback: SyntaxError")
        tools = [
            {
                "name": "tool1",
                "entrypoint": "mod:func",
                "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
            }
        ]
        status, log, reason, _candidate = step_smoke(sandbox, tools)
        assert status == "failed"

    def test_smoke_caps_tools(self):
        """Smoke should only test up to VERIFICATION_SMOKE_MAX_TOOLS tools."""
        from app.verification.steps import step_smoke
        from unittest.mock import MagicMock, patch
        sandbox = MagicMock()
        sandbox.run_python_code.return_value = (
            True,
            'SMOKE_JSON:{"status":"ok","return_type":"dict"}',
        )

        tools = [
            {
                "name": f"tool_{i}",
                "entrypoint": f"mod{i}:func",
                "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
            }
            for i in range(10)
        ]

        with patch("app.verification.steps.settings") as mock_settings:
            mock_settings.VERIFICATION_SMOKE_MAX_TOOLS = 3
            mock_settings.VERIFICATION_SMOKE_BUDGET_SECONDS = 60
            status, log, reason, _candidate = step_smoke(sandbox, tools)

        assert status == "passed"
        assert sandbox.run_python_code.call_count == 3
        assert "cap" in log.lower()


class TestStepTests:

    def test_tests_no_test_dir(self):
        from app.verification.steps import step_tests
        from unittest.mock import MagicMock
        sandbox = MagicMock()
        sandbox.has_tests.return_value = False
        ok, log = step_tests(sandbox)
        assert ok is False
        assert "No test directory" in log


# --- Smoke classification tests (Phase 1 core) ---

class TestClassifySmokeError:
    """7 core classification test cases from the Phase 1 plan."""

    def test_1_credential_boundary_with_env_reqs(self):
        """ValueError 'missing api key' + env_reqs=True → credential_boundary_reached, inconclusive."""
        ctx = SmokeContext(tool_name="test", has_required_env_requirements=True, input_schema_present=True)
        reason = classify_smoke_error("ValueError", "missing api key", ctx)
        assert reason == "credential_boundary_reached"
        assert REASON_VERDICTS[reason][0] == "inconclusive"

    def test_2_unsupported_operation_space(self):
        """ValueError 'Unknown operation: read' → unsupported_operation_space, inconclusive."""
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("ValueError", "Unknown operation: read", ctx)
        assert reason == "unsupported_operation_space"
        assert REASON_VERDICTS[reason][0] == "inconclusive"

    def test_3_connection_error_with_network(self):
        """ConnectionError + declares_network=True → acceptable_external_dependency, passed."""
        ctx = SmokeContext(tool_name="test", declares_network_access=True)
        reason = classify_smoke_error("ConnectionError", "Connection refused", ctx)
        assert reason == "acceptable_external_dependency"
        assert REASON_VERDICTS[reason][0] == "passed"

    def test_4_connection_error_without_network(self):
        """ConnectionError + declares_network=False → external_network_blocked, inconclusive."""
        ctx = SmokeContext(tool_name="test", declares_network_access=False)
        reason = classify_smoke_error("ConnectionError", "Connection refused", ctx)
        assert reason == "external_network_blocked"
        assert REASON_VERDICTS[reason][0] == "inconclusive"

    def test_5_type_error_contract_break_with_schema(self):
        """TypeError 'unexpected keyword argument' + schema=True → fatal_type_error, failed."""
        ctx = SmokeContext(tool_name="test", input_schema_present=True)
        reason = classify_smoke_error("TypeError", "got an unexpected keyword argument 'foo'", ctx)
        assert reason == "fatal_type_error"
        assert REASON_VERDICTS[reason][0] == "failed"

    def test_6_type_error_contract_break_without_schema(self):
        """TypeError 'unexpected keyword argument' + schema=False → schema_signature_mismatch, inconclusive."""
        ctx = SmokeContext(tool_name="test", input_schema_present=False)
        reason = classify_smoke_error("TypeError", "got an unexpected keyword argument 'foo'", ctx)
        assert reason == "schema_signature_mismatch"
        assert REASON_VERDICTS[reason][0] == "inconclusive"

    def test_7_file_not_found_is_not_passed(self):
        """FileNotFoundError → invalid_test_input (test input pointed to non-existent file), inconclusive."""
        ctx = SmokeContext(tool_name="test", declares_network_access=False)
        reason = classify_smoke_error("FileNotFoundError", "No such file: /tmp/foo", ctx)
        assert reason == "invalid_test_input"
        assert REASON_VERDICTS[reason][0] == "inconclusive"


class TestClassifySmokeErrorExtended:
    """Additional classification edge cases."""

    def test_import_error_is_fatal(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("ImportError", "No module named 'foo'", ctx)
        assert reason == "fatal_import_during_smoke"
        assert REASON_VERDICTS[reason][0] == "failed"

    def test_module_not_found_is_fatal(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("ModuleNotFoundError", "No module named 'bar'", ctx)
        assert reason == "fatal_import_during_smoke"
        assert REASON_VERDICTS[reason][0] == "failed"

    def test_syntax_error_is_fatal(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("SyntaxError", "invalid syntax", ctx)
        assert reason == "fatal_runtime_error"
        assert REASON_VERDICTS[reason][0] == "failed"

    def test_name_error_is_fatal(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("NameError", "name 'x' is not defined", ctx)
        assert reason == "fatal_runtime_error"
        assert REASON_VERDICTS[reason][0] == "failed"

    def test_attribute_error_no_attribute(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("AttributeError", "'NoneType' object has no attribute 'run'", ctx)
        assert reason == "schema_signature_mismatch"

    def test_attribute_error_other(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("AttributeError", "module has no such thing", ctx)
        assert reason == "unknown_smoke_condition"

    def test_type_error_generic(self):
        """Generic TypeError (not contract break) → unknown_smoke_condition."""
        ctx = SmokeContext(tool_name="test", input_schema_present=True)
        reason = classify_smoke_error("TypeError", "cannot unpack non-iterable NoneType", ctx)
        assert reason == "unknown_smoke_condition"

    def test_value_error_with_input_rejection(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("ValueError", "invalid value for parameter 'mode'", ctx)
        assert reason == "invalid_test_input"

    def test_value_error_ambiguous(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("ValueError", "something went wrong internally", ctx)
        assert reason == "unknown_smoke_condition"

    def test_credential_pattern_takes_priority(self):
        """Credential keywords should take priority over operation keywords."""
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("ValueError", "api key is required for this operation", ctx)
        assert reason == "credential_boundary_reached"

    def test_timeout_error_with_network(self):
        ctx = SmokeContext(tool_name="test", declares_network_access=True)
        reason = classify_smoke_error("TimeoutError", "Connection timed out", ctx)
        assert reason == "acceptable_external_dependency"

    def test_timeout_error_without_network(self):
        ctx = SmokeContext(tool_name="test", declares_network_access=False)
        reason = classify_smoke_error("TimeoutError", "Connection timed out", ctx)
        assert reason == "external_network_blocked"

    def test_value_error_missing_required_positional(self):
        """ValueError wrapping a missing-arg message → signature-level classification."""
        ctx = SmokeContext(tool_name="test", input_schema_present=True)
        reason = classify_smoke_error("ValueError", "missing 2 required positional arguments", ctx)
        assert reason == "fatal_type_error"

    def test_value_error_missing_required_positional_no_schema(self):
        ctx = SmokeContext(tool_name="test", input_schema_present=False)
        reason = classify_smoke_error("ValueError", "missing 2 required positional arguments", ctx)
        assert reason == "schema_signature_mismatch"

    def test_unknown_exception_type(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("CustomException", "something bad", ctx)
        assert reason == "unknown_smoke_condition"


class TestClassifyTimeout:

    def test_timeout_with_network(self):
        ctx = SmokeContext(tool_name="test", declares_network_access=True)
        assert classify_timeout(ctx) == "external_network_blocked"

    def test_timeout_without_network(self):
        ctx = SmokeContext(tool_name="test", declares_network_access=False)
        assert classify_timeout(ctx) == "fatal_timeout"

    def test_timeout_heavy_import(self):
        ctx = SmokeContext(
            tool_name="test",
            declares_network_access=False,
            python_dependencies=frozenset({"torch", "numpy"}),
        )
        assert classify_timeout(ctx) == "heavy_import_timeout"

    def test_timeout_heavy_import_network_takes_priority(self):
        ctx = SmokeContext(
            tool_name="test",
            declares_network_access=True,
            python_dependencies=frozenset({"torch"}),
        )
        assert classify_timeout(ctx) == "external_network_blocked"

    def test_timeout_non_heavy_deps(self):
        ctx = SmokeContext(
            tool_name="test",
            declares_network_access=False,
            python_dependencies=frozenset({"requests", "numpy"}),
        )
        assert classify_timeout(ctx) == "fatal_timeout"


class TestBuildSmokeContext:

    def test_basic_context(self):
        tool = {"name": "test-tool"}
        ctx = build_smoke_context(tool)
        assert ctx.tool_name == "test-tool"
        assert ctx.declares_network_access is False
        assert ctx.input_schema_present is False

    def test_network_level_full(self):
        tool = {"name": "test", "network_level": "full"}
        ctx = build_smoke_context(tool)
        assert ctx.declares_network_access is True

    def test_network_level_restricted(self):
        """'restricted' counts as declares_network_access=False."""
        tool = {"name": "test", "network_level": "restricted"}
        ctx = build_smoke_context(tool)
        assert ctx.declares_network_access is False

    def test_network_level_none(self):
        tool = {"name": "test", "network_level": "none"}
        ctx = build_smoke_context(tool)
        assert ctx.declares_network_access is False

    def test_env_requirements(self):
        tool = {
            "name": "test",
            "env_requirements": [
                {"name": "API_KEY", "required": True},
                {"name": "OPTIONAL_VAR", "required": False},
            ],
        }
        ctx = build_smoke_context(tool)
        assert ctx.has_required_env_requirements is True
        assert ctx.missing_required_env_vars == ["API_KEY"]

    def test_schema_present(self):
        tool = {
            "name": "test",
            "input_schema": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
            },
        }
        ctx = build_smoke_context(tool)
        assert ctx.input_schema_present is True

    def test_enum_hints_detected(self):
        tool = {
            "name": "test",
            "input_schema": {
                "type": "object",
                "properties": {"mode": {"type": "string", "enum": ["a", "b"]}},
            },
        }
        ctx = build_smoke_context(tool)
        assert ctx.input_has_enum_hints is True

    def test_python_dependencies(self):
        tool = {
            "name": "test",
            "python_dependencies": ["torch>=2.0", "numpy", "sentence-transformers[gpu]"],
        }
        ctx = build_smoke_context(tool)
        assert ctx.python_dependencies == frozenset({"torch", "numpy", "sentence_transformers"})


class TestToolResultFromCandidates:
    """Test the _tool_result_from_candidates logic."""

    def test_passed_wins(self):
        from app.verification.steps import _tool_result_from_candidates
        results = [
            {"reason": "unknown_smoke_condition", "verdict": "inconclusive"},
            {"reason": "ok", "verdict": "passed"},
        ]
        reason, verdict = _tool_result_from_candidates(results)
        assert verdict == "passed"
        assert reason == "ok"

    def test_fatal_beats_inconclusive(self):
        """Rule 3: Fatal darf nie von Inconclusive überschrieben werden."""
        from app.verification.steps import _tool_result_from_candidates
        results = [
            {"reason": "fatal_runtime_error", "verdict": "failed"},
            {"reason": "unknown_smoke_condition", "verdict": "inconclusive"},
        ]
        reason, verdict = _tool_result_from_candidates(results)
        assert verdict == "failed"
        assert reason == "fatal_runtime_error"

    def test_inconclusive_only(self):
        from app.verification.steps import _tool_result_from_candidates
        results = [
            {"reason": "unsupported_operation_space", "verdict": "inconclusive"},
        ]
        reason, verdict = _tool_result_from_candidates(results)
        assert verdict == "inconclusive"
        assert reason == "unsupported_operation_space"

    def test_empty_results(self):
        from app.verification.steps import _tool_result_from_candidates
        reason, verdict = _tool_result_from_candidates([])
        assert verdict == "inconclusive"
        assert reason == "invalid_test_input"

    def test_passed_takes_priority_over_fatal(self):
        """If any candidate passed, tool passes regardless of other candidates."""
        from app.verification.steps import _tool_result_from_candidates
        results = [
            {"reason": "ok", "verdict": "passed"},
            {"reason": "fatal_type_error", "verdict": "failed"},
        ]
        reason, verdict = _tool_result_from_candidates(results)
        assert verdict == "passed"
        assert reason == "ok"


class TestReasonTaxonomy:
    """Verify taxonomy is consistent."""

    def test_all_fatal_reasons_map_to_failed(self):
        for reason in FATAL_REASONS:
            assert reason in REASON_VERDICTS
            assert REASON_VERDICTS[reason][0] == "failed", f"{reason} should map to failed"

    def test_reason_verdicts_complete(self):
        """All reasons map to valid statuses."""
        valid_statuses = {"passed", "inconclusive", "failed"}
        for reason, (status, _) in REASON_VERDICTS.items():
            assert status in valid_statuses, f"{reason} has invalid status {status}"


# --- Phase 2A tests ---

class TestIsIncompleteSchema:
    """Block A: Schema Repair."""

    def test_object_without_properties(self):
        assert is_incomplete_schema({"type": "object"}) is True

    def test_object_with_properties(self):
        assert is_incomplete_schema({"type": "object", "properties": {"x": {}}}) is False

    def test_array_without_items(self):
        assert is_incomplete_schema({"type": "array"}) is True

    def test_array_with_items(self):
        assert is_incomplete_schema({"type": "array", "items": {"type": "string"}}) is False

    def test_none_schema(self):
        assert is_incomplete_schema(None) is True

    def test_primitive_schema(self):
        assert is_incomplete_schema({"type": "string"}) is False

    def test_empty_dict(self):
        assert is_incomplete_schema({}) is True

    def test_object_with_empty_properties(self):
        """Empty properties dict is useless for input generation → incomplete."""
        assert is_incomplete_schema({"type": "object", "properties": {}}) is True


class TestFindOperationField:
    """Block B: Operation field detection."""

    def test_required_operation_without_enum(self):
        schema = {
            "type": "object",
            "properties": {"operation": {"type": "string"}, "text": {"type": "string"}},
            "required": ["operation", "text"],
        }
        assert _find_operation_field(schema) == "operation"

    def test_operation_with_enum_skipped(self):
        schema = {
            "type": "object",
            "properties": {"operation": {"type": "string", "enum": ["create", "delete"]}},
            "required": ["operation"],
        }
        assert _find_operation_field(schema) is None

    def test_operation_with_default_skipped(self):
        schema = {
            "type": "object",
            "properties": {"action": {"type": "string", "default": "read"}},
            "required": ["action"],
        }
        assert _find_operation_field(schema) is None

    def test_no_operation_field(self):
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}, "count": {"type": "integer"}},
            "required": ["text"],
        }
        assert _find_operation_field(schema) is None

    def test_optional_operation_second_pass(self):
        """Operation field not in required but in properties → found on second pass."""
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}, "operation": {"type": "string"}},
            "required": ["text"],
        }
        assert _find_operation_field(schema) == "operation"

    def test_non_object_schema(self):
        assert _find_operation_field({"type": "array"}) is None

    def test_no_properties(self):
        assert _find_operation_field({"type": "object"}) is None


class TestGenerateCandidatesPhase2A:
    """Block B: Improved candidate generation."""

    def test_operation_field_generates_two_candidates(self):
        schema = {
            "type": "object",
            "properties": {"operation": {"type": "string"}, "text": {"type": "string"}},
            "required": ["operation", "text"],
        }
        candidates = generate_candidates(schema)
        assert len(candidates) == 2
        ops = {c["operation"] for c in candidates}
        assert len(ops) == 2  # two different operation values

    def test_no_operation_field_normal_behavior(self):
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }
        candidates = generate_candidates(schema)
        assert len(candidates) >= 1
        assert "text" in candidates[0]

    def test_name_hints_operation_is_list(self):
        assert NAME_HINTS["operation"] == "list"
        assert NAME_HINTS["action"] == "list"

    def test_name_hints_command_is_help(self):
        assert NAME_HINTS["command"] == "help"

    def test_schema_examples_takes_priority(self):
        """Schema examples should be first candidate, before generate_test_input."""
        example = {"operation": "create_event", "title": "Meeting"}
        schema = {
            "type": "object",
            "properties": {"operation": {"type": "string"}, "title": {"type": "string"}},
            "required": ["operation", "title"],
            "examples": [example],
        }
        candidates = generate_candidates(schema)
        assert candidates[0] == example

    def test_schema_examples_with_operation_field(self):
        """With examples AND operation field: example first, operation alt second."""
        example = {"operation": "create_event", "title": "Test"}
        schema = {
            "type": "object",
            "properties": {"operation": {"type": "string"}, "title": {"type": "string"}},
            "required": ["operation", "title"],
            "examples": [example],
        }
        candidates = generate_candidates(schema)
        assert len(candidates) == 2
        assert candidates[0] == example
        # Second candidate should have a different operation value
        assert candidates[1]["operation"] != candidates[0]["operation"]

    def test_deduplication(self):
        """If schema example equals generate_test_input, only one copy."""
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "examples": [{"text": "Hello world"}],
        }
        candidates = generate_candidates(schema)
        # "Hello world" comes from example, generate_test_input also produces it via NAME_HINTS
        unique = []
        for c in candidates:
            if c not in unique:
                unique.append(c)
        assert len(candidates) == len(unique)

    def test_operation_candidates_order(self):
        """OPERATION_CANDIDATES starts with list, get, search."""
        assert OPERATION_CANDIDATES[:3] == ["list", "get", "search"]


class TestCollectStubPaths:
    """Block C: File stub collection. Returns (text_stubs, binary_stubs) tuple."""

    def test_text_file_collected(self):
        text, binary = _collect_stub_paths({"path": "/tmp/test.txt"})
        assert text == ["/tmp/test.txt"]
        assert binary == []

    def test_non_path_param_ignored(self):
        text, binary = _collect_stub_paths({"name": "test"})
        assert text == []
        assert binary == []

    def test_binary_format_collected_separately(self):
        text, binary = _collect_stub_paths({"file_path": "/tmp/foo.pdf"})
        assert text == []
        assert binary == ["/tmp/foo.pdf"]

    def test_csv_file_collected(self):
        text, binary = _collect_stub_paths({"file_path": "/tmp/data.csv", "format": "csv"})
        assert text == ["/tmp/data.csv"]

    def test_output_file_excluded(self):
        """output_file is not in FILE_PATH_PARAMS → not stubbed."""
        text, binary = _collect_stub_paths({"output_file": "/tmp/out.json"})
        assert text == []
        assert binary == []

    def test_no_slash_not_collected(self):
        """Values without path separators are not file paths."""
        text, binary = _collect_stub_paths({"path": "just-a-name"})
        assert text == []
        assert binary == []

    def test_multiple_paths(self):
        text, binary = _collect_stub_paths({
            "path": "/tmp/test.txt",
            "source_file": "/tmp/source.json",
        })
        assert len(text) == 2
        assert "/tmp/test.txt" in text
        assert "/tmp/source.json" in text

    def test_json_file_collected(self):
        text, binary = _collect_stub_paths({"file": "/tmp/data.json"})
        assert text == ["/tmp/data.json"]

    def test_no_extension_collected(self):
        """Files without extension are still collected (no ext means no binary check fails)."""
        text, binary = _collect_stub_paths({"path": "/tmp/testfile"})
        assert text == ["/tmp/testfile"]

    def test_new_name_hints_present(self):
        """Phase 2A NAME_HINTS additions."""
        assert NAME_HINTS["file"] == "/tmp/agentnode_verify/test.txt"
        assert NAME_HINTS["input_file"] == "/tmp/agentnode_verify/test.txt"
        assert NAME_HINTS["output_file"] == "/tmp/agentnode_verify/output.txt"
        assert NAME_HINTS["directory"] == "/tmp/agentnode_verify"
        assert NAME_HINTS["source_language"] == "en"
        assert NAME_HINTS["target_language"] == "de"


# --- Phase 2B: Active Enum Probe tests ---

class TestExtractEnumValues:

    def test_bracket_list_quoted(self):
        msg = "ValueError: Invalid operation. Choose from ['create_event', 'list_events', 'delete_event']"
        values, confidence = extract_enum_values(msg)
        assert confidence == "high"
        assert values == ["create_event", "list_events", "delete_event"]

    def test_bracket_list_single_quotes(self):
        msg = "Must be one of ['read', 'write']"
        values, confidence = extract_enum_values(msg)
        assert confidence == "high"
        assert set(values) == {"read", "write"}

    def test_comma_list_after_keyword(self):
        msg = "Supported operations: create, read, update, delete"
        values, confidence = extract_enum_values(msg)
        assert confidence == "medium"
        assert len(values) >= 2

    def test_no_enum_found(self):
        msg = "Something went wrong with the input"
        values, confidence = extract_enum_values(msg)
        assert confidence == "none"
        assert values == []

    def test_empty_message(self):
        values, confidence = extract_enum_values("")
        assert confidence == "none"

    def test_comma_list_single_value(self):
        """Single value comma list should not match (needs >= 2)."""
        msg = "Allowed: only_one"
        values, confidence = extract_enum_values(msg)
        assert confidence == "none"

    def test_bracket_list_with_double_quotes(self):
        msg = 'Choose from ["list_users", "create_user", "delete_user"]'
        values, confidence = extract_enum_values(msg)
        assert confidence == "high"
        assert "list_users" in values

    def test_hyphened_values(self):
        msg = "Available: list-events, create-event, delete-event"
        values, confidence = extract_enum_values(msg)
        assert confidence == "medium"
        assert "list-events" in values


class TestSortBySafety:

    def test_read_only_first(self):
        values = ["delete_event", "list_events", "create_event", "get_event"]
        sorted_v = _sort_by_safety(values)
        assert sorted_v[0].startswith("list") or sorted_v[0].startswith("get")

    def test_safe_prefixes_ordered(self):
        values = ["search_items", "list_items", "delete_items"]
        sorted_v = _sort_by_safety(values)
        assert sorted_v[0] == "list_items"
        assert sorted_v[1] == "search_items"
        assert sorted_v[2] == "delete_items"

    def test_no_safe_prefix(self):
        values = ["create", "update", "delete"]
        sorted_v = _sort_by_safety(values)
        # All mutating, alphabetical within unsafe
        assert len(sorted_v) == 3


class TestBuildProbeCandidate:

    def test_builds_from_bracket_list(self):
        base = {"operation": "list", "text": "hello"}
        msg = "Invalid operation. Choose from ['create_event', 'list_events', 'delete_event']"
        result = build_probe_candidate(base, "operation", msg)
        assert result is not None
        assert result["operation"] == "list_events"  # Safest (starts with "list")
        assert result["text"] == "hello"  # Preserved

    def test_returns_none_for_garbage(self):
        base = {"operation": "list"}
        msg = "Something went wrong"
        result = build_probe_candidate(base, "operation", msg)
        assert result is None

    def test_returns_none_for_empty_msg(self):
        result = build_probe_candidate({"op": "x"}, "op", "")
        assert result is None


class TestFindOperationFieldInInput:

    def test_finds_operation_field(self):
        test_input = {"operation": "list", "text": "hello"}
        schema = {"type": "object", "properties": {"operation": {}, "text": {}}}
        assert _find_operation_field_in_input(test_input, schema) == "operation"

    def test_finds_action_field(self):
        test_input = {"action": "get", "data": "test"}
        schema = {"type": "object", "properties": {"action": {}, "data": {}}}
        assert _find_operation_field_in_input(test_input, schema) == "action"

    def test_returns_none_for_no_op_field(self):
        test_input = {"text": "hello", "count": 5}
        schema = {"type": "object", "properties": {"text": {}, "count": {}}}
        assert _find_operation_field_in_input(test_input, schema) is None


# --- Phase 3A: Reason Quality tests ---

class TestClassifySmokeErrorPhase3A:
    """New classification tests for Phase 3A reasons."""

    def test_not_implemented_error(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("NotImplementedError", "replace this with your implementation", ctx)
        assert reason == "not_implemented"
        assert REASON_VERDICTS[reason][0] == "inconclusive"

    def test_not_implemented_any_message(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("NotImplementedError", "any message at all", ctx)
        assert reason == "not_implemented"

    def test_system_dependency_playwright(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("Error", "BrowserType.launch failed: executable doesn't exist", ctx)
        assert reason == "missing_system_dependency"
        assert REASON_VERDICTS[reason][0] == "inconclusive"

    def test_system_dependency_chromium(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("RuntimeError", "chromium is not installed", ctx)
        assert reason == "missing_system_dependency"

    def test_system_dependency_ffmpeg(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("FileNotFoundError", "ffmpeg not found on PATH", ctx)
        assert reason == "missing_system_dependency"

    def test_binary_input_pdf(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("ValueError", "not a valid PDF file", ctx)
        assert reason == "needs_binary_input"
        assert REASON_VERDICTS[reason][0] == "inconclusive"

    def test_binary_input_image(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("ValueError", "cannot read image file", ctx)
        assert reason == "needs_binary_input"

    def test_file_exists_error(self):
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("FileExistsError", "file already exists", ctx)
        assert reason == "invalid_test_input"

    # Priority tests from the plan
    def test_timeout_with_network_not_fatal(self):
        """TimeoutError + declares_network → acceptable, not fatal_timeout."""
        ctx = SmokeContext(tool_name="test", declares_network_access=True)
        reason = classify_smoke_error("TimeoutError", "Connection timed out", ctx)
        assert reason == "acceptable_external_dependency"

    def test_not_implemented_over_unknown(self):
        """NotImplementedError → not_implemented, not unknown_smoke_condition."""
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("NotImplementedError", "weird message", ctx)
        assert reason == "not_implemented"

    def test_value_error_binary_not_input(self):
        """ValueError + 'not a valid PDF' → needs_binary_input, not invalid_test_input."""
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("ValueError", "not a valid PDF", ctx)
        assert reason == "needs_binary_input"

    def test_file_not_found_ffmpeg_is_system_dep(self):
        """FileNotFoundError + 'ffmpeg not found' → missing_system_dependency, not invalid_test_input."""
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("FileNotFoundError", "ffmpeg not found", ctx)
        assert reason == "missing_system_dependency"

    def test_browser_launch_is_system_dep(self):
        """Error + 'BrowserType.launch' → missing_system_dependency."""
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("Error", "BrowserType.launch: executable doesn't exist", ctx)
        assert reason == "missing_system_dependency"

    def test_value_error_invalid_without_binary(self):
        """ValueError + 'invalid value' without binary match → invalid_test_input."""
        ctx = SmokeContext(tool_name="test")
        reason = classify_smoke_error("ValueError", "invalid value for parameter 'mode'", ctx)
        assert reason == "invalid_test_input"


class TestNewReasonTaxonomy:
    """Verify new Phase 3A reasons are in taxonomy."""

    def test_new_reasons_exist(self):
        assert "missing_system_dependency" in REASON_VERDICTS
        assert "not_implemented" in REASON_VERDICTS
        assert "needs_binary_input" in REASON_VERDICTS

    def test_new_reasons_are_inconclusive(self):
        assert REASON_VERDICTS["missing_system_dependency"][0] == "inconclusive"
        assert REASON_VERDICTS["not_implemented"][0] == "inconclusive"
        assert REASON_VERDICTS["needs_binary_input"][0] == "inconclusive"


class TestDominantReason:
    """Test the _dominant_reason helper."""

    def test_all_same(self):
        assert _dominant_reason(["ok", "ok", "ok"]) == "ok"

    def test_fatal_dominates(self):
        assert _dominant_reason(["ok", "fatal_runtime_error", "ok"]) == "fatal_runtime_error"

    def test_specific_over_generic(self):
        assert _dominant_reason(["ok", "needs_credentials"]) == "needs_credentials"

    def test_empty_returns_none(self):
        assert _dominant_reason([]) is None

    def test_unknown_last_resort(self):
        assert _dominant_reason(["unknown_smoke_condition", "unknown_smoke_condition"]) == "unknown_smoke_condition"


class TestStubPathsPhase3A:
    """Phase 3A: Updated stub paths use /tmp/agentnode_verify/ prefix."""

    def test_stub_paths_use_verify_prefix(self):
        text, binary = _collect_stub_paths({"path": "/tmp/agentnode_verify/test.txt"})
        assert text == ["/tmp/agentnode_verify/test.txt"]

    def test_name_hints_paths_updated(self):
        assert NAME_HINTS["path"] == "/tmp/agentnode_verify/test.txt"
        assert NAME_HINTS["file_path"] == "/tmp/agentnode_verify/test.txt"


# --- Phase 5-7A tests ---

class TestScoringPhase6:
    """Phase 6B: ScoreResult with explanation and confidence."""

    def test_compute_score_result_passed(self):
        from app.verification.scoring import compute_score_result, ScoreResult
        from unittest.mock import MagicMock
        vr = MagicMock()
        vr.install_status = "passed"
        vr.import_status = "passed"
        vr.smoke_status = "passed"
        vr.smoke_reason = "ok"
        vr.tests_status = "passed"
        vr.tests_auto_generated = False
        vr.reliability = 1.0
        vr.determinism_score = 1.0
        vr.contract_valid = True
        vr.contract_details = None
        vr.warnings_count = 0
        vr.verification_mode = "real"
        vr.stability_log = [{"ok": True}, {"ok": True}, {"ok": True}]
        vr.install_duration_ms = 2000
        vr.smoke_confidence = None

        result = compute_score_result(vr)
        assert isinstance(result, ScoreResult)
        assert result.score == 95  # Max: 15+15+25+15+10+10+5 = 95
        assert result.tier == "gold"
        assert result.confidence == "high"
        assert "install" in result.breakdown
        assert result.breakdown["install"].points == 15

    def test_compute_score_result_credential_boundary(self):
        from app.verification.scoring import compute_score_result
        from unittest.mock import MagicMock
        vr = MagicMock()
        vr.install_status = "passed"
        vr.import_status = "passed"
        vr.smoke_status = "inconclusive"
        vr.smoke_reason = "credential_boundary_reached"
        vr.tests_status = "passed"
        vr.tests_auto_generated = True
        vr.reliability = None
        vr.determinism_score = None
        vr.contract_valid = None
        vr.contract_details = None
        vr.warnings_count = 0
        vr.verification_mode = "real"
        vr.stability_log = None
        vr.install_duration_ms = 1500
        vr.smoke_confidence = "high"

        result = compute_score_result(vr)
        assert result.tier == "partial"  # Hard cap for credential boundary
        assert result.confidence == "low"  # Inconclusive + credential = low

    def test_tier_cap_smoke_not_passed(self):
        from app.verification.scoring import apply_tier_caps, cap_tier
        from unittest.mock import MagicMock
        vr = MagicMock()
        vr.smoke_status = "inconclusive"
        vr.smoke_reason = "unknown_smoke_condition"
        vr.contract_valid = True
        vr.contract_details = None
        vr.verification_mode = "real"
        vr.reliability = None

        tier = apply_tier_caps(95, "gold", vr)
        assert tier == "verified"  # Capped from gold to verified

    def test_cap_tier_never_promotes(self):
        from app.verification.scoring import cap_tier
        assert cap_tier("partial", "gold") == "partial"
        assert cap_tier("gold", "verified") == "verified"
        assert cap_tier("verified", "verified") == "verified"
        assert cap_tier("unverified", "gold") == "unverified"

    def test_gold_requires_all_conditions(self):
        from app.verification.scoring import _qualifies_for_gold
        from unittest.mock import MagicMock
        vr = MagicMock()
        vr.smoke_status = "passed"
        vr.smoke_reason = "ok"
        vr.contract_valid = True
        vr.contract_details = None
        vr.verification_mode = "real"
        vr.reliability = 1.0
        assert _qualifies_for_gold(95, vr) is True

        # Missing contract
        vr.contract_valid = False
        assert _qualifies_for_gold(95, vr) is False

    def test_backward_compatible_compute_tool_score(self):
        from app.verification.scoring import compute_tool_score
        from unittest.mock import MagicMock
        vr = MagicMock()
        vr.install_status = "passed"
        vr.import_status = "passed"
        vr.smoke_status = "passed"
        vr.smoke_reason = "ok"
        vr.tests_status = "not_present"
        vr.tests_auto_generated = False
        vr.reliability = None
        vr.determinism_score = None
        vr.contract_valid = True
        vr.contract_details = None
        vr.warnings_count = 0
        vr.verification_mode = "real"
        vr.stability_log = None
        vr.install_duration_ms = None
        vr.smoke_confidence = None

        score, tier, breakdown = compute_tool_score(vr)
        assert isinstance(score, int)
        assert isinstance(tier, str)
        assert isinstance(breakdown, dict)
        assert all(isinstance(v, int) for v in breakdown.values())


class TestContractValidation:
    """Phase 6A: Contract validation."""

    def test_valid_contract(self):
        from app.verification.contract import validate_return
        smoke_data = {
            "status": "ok",
            "return_type": "dict",
            "return_hash": "abc123",
            "is_none": False,
            "is_serializable": True,
            "return_keys": ["key1", "key2"],
            "return_length": 2,
        }
        result = validate_return(smoke_data, "test_tool", {"text": "hello"})
        assert result["valid"] is True
        assert result["points"] == 10
        assert len(result["checks"]) == 4

    def test_none_return_invalid(self):
        from app.verification.contract import validate_return
        smoke_data = {
            "status": "ok",
            "return_type": "NoneType",
            "return_hash": "abc",
            "is_none": True,
            "is_serializable": True,
            "return_keys": None,
            "return_length": None,
        }
        result = validate_return(smoke_data, "test_tool", {})
        assert result["valid"] is False
        assert result["points"] < 10

    def test_not_serializable_invalid(self):
        from app.verification.contract import validate_return
        smoke_data = {
            "status": "ok",
            "return_type": "MyObject",
            "return_hash": "abc",
            "is_none": False,
            "is_serializable": False,
            "return_keys": None,
            "return_length": None,
        }
        result = validate_return(smoke_data, "test_tool", {})
        assert result["valid"] is False

    def test_semantic_sanity_summary(self):
        from app.verification.contract import semantic_sanity_check
        hints = semantic_sanity_check(
            "summarize_text",
            {"text": "This is a long text " * 10},
            {"status": "ok", "return_type": "str", "return_length": 500},
        )
        assert len(hints) >= 1

    def test_semantic_sanity_no_false_positive(self):
        from app.verification.contract import semantic_sanity_check
        hints = semantic_sanity_check(
            "count_words",
            {"text": "hello world"},
            {"status": "ok", "return_type": "dict", "return_length": 2},
        )
        assert len(hints) == 0

    def test_error_status_returns_zero(self):
        from app.verification.contract import validate_return
        result = validate_return({"status": "error"}, "test", {})
        assert result["valid"] is False
        assert result["points"] == 0


class TestCredentialBoundaryDetection:
    """Phase 7A: Credential boundary detection."""

    def test_auth_exception_high_confidence(self):
        from app.verification.smoke_context import classify_credential_boundary, SmokeContext
        ctx = SmokeContext(tool_name="test")
        reason, confidence = classify_credential_boundary("AuthenticationError", "invalid key", ctx)
        assert reason == "credential_boundary_reached"
        assert confidence == "high"

    def test_api_key_error_high_confidence(self):
        from app.verification.smoke_context import classify_credential_boundary, SmokeContext
        ctx = SmokeContext(tool_name="test")
        reason, confidence = classify_credential_boundary("APIKeyError", "missing key", ctx)
        assert reason == "credential_boundary_reached"
        assert confidence == "high"

    def test_credential_pattern_with_env_reqs_high(self):
        from app.verification.smoke_context import classify_credential_boundary, SmokeContext
        ctx = SmokeContext(tool_name="test", has_required_env_requirements=True)
        reason, confidence = classify_credential_boundary("ValueError", "api key required", ctx)
        assert reason == "credential_boundary_reached"
        assert confidence == "high"

    def test_credential_pattern_without_env_reqs_medium(self):
        from app.verification.smoke_context import classify_credential_boundary, SmokeContext
        ctx = SmokeContext(tool_name="test", has_required_env_requirements=False)
        reason, confidence = classify_credential_boundary("ValueError", "api key required", ctx)
        assert reason == "credential_boundary_reached"
        assert confidence == "medium"

    def test_http_401_medium(self):
        from app.verification.smoke_context import classify_credential_boundary, SmokeContext
        ctx = SmokeContext(tool_name="test")
        reason, confidence = classify_credential_boundary("HTTPError", "401 unauthorized", ctx)
        assert reason == "credential_boundary_reached"
        assert confidence == "medium"

    def test_no_credential_boundary(self):
        from app.verification.smoke_context import classify_credential_boundary, SmokeContext
        ctx = SmokeContext(tool_name="test")
        reason, confidence = classify_credential_boundary("ValueError", "bad format", ctx)
        assert reason is None
        assert confidence is None

    def test_credential_boundary_in_reason_verdicts(self):
        """credential_boundary_reached must be in REASON_VERDICTS."""
        assert "credential_boundary_reached" in REASON_VERDICTS
        assert REASON_VERDICTS["credential_boundary_reached"][0] == "inconclusive"


class TestConfidenceLevel:
    """Phase 6B: Confidence level computation."""

    def test_high_confidence(self):
        from app.verification.scoring import compute_confidence
        from unittest.mock import MagicMock
        vr = MagicMock()
        vr.smoke_status = "passed"
        vr.contract_valid = True
        vr.contract_details = None
        vr.reliability = 1.0
        vr.tests_status = "passed"
        vr.tests_auto_generated = False
        vr.smoke_reason = "ok"
        assert compute_confidence(vr) == "high"

    def test_low_confidence_inconclusive(self):
        from app.verification.scoring import compute_confidence
        from unittest.mock import MagicMock
        vr = MagicMock()
        vr.smoke_status = "inconclusive"
        vr.contract_valid = None
        vr.contract_details = None
        vr.reliability = None
        vr.tests_status = "not_present"
        vr.tests_auto_generated = False
        vr.smoke_reason = "credential_boundary_reached"
        assert compute_confidence(vr) == "low"
