"""Tests for the verification pipeline."""

import pytest

from app.verification.schema_generator import generate_test_input, _generate_value


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
        assert result == {"name": "test"}

    def test_integer_type(self):
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        }
        result = generate_test_input(schema)
        assert result == {"count": 1}

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
        assert result["name"] == "test"
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
        assert result == {"config": {"timeout": 1}}

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
        status, log = step_smoke(sandbox, [])
        assert status == "skipped"

    def test_smoke_returns_string_status(self):
        """Smoke step should return status string, not bool."""
        from app.verification.steps import step_smoke
        from unittest.mock import MagicMock
        sandbox = MagicMock()
        sandbox.run_python_code.return_value = (True, "RESULT_TYPE:dict")
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
        status, log = step_smoke(sandbox, tools)
        assert status == "passed"
        assert isinstance(status, str)
        assert "[PASS]" in log

    def test_smoke_inconclusive_on_value_error(self):
        """ValueError should result in inconclusive, not pass."""
        from app.verification.steps import step_smoke
        from unittest.mock import MagicMock
        sandbox = MagicMock()
        sandbox.run_python_code.return_value = (True, "INCONCLUSIVE_ERROR:ValueError:bad input")
        tools = [
            {
                "name": "tool1",
                "entrypoint": "mod:func",
                "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
            }
        ]
        status, log = step_smoke(sandbox, tools)
        assert status == "inconclusive"
        assert "[INCONCLUSIVE]" in log

    def test_smoke_failed_on_fatal_error(self):
        """Fatal errors (TypeError etc) should result in failed."""
        from app.verification.steps import step_smoke
        from unittest.mock import MagicMock
        sandbox = MagicMock()
        sandbox.run_python_code.return_value = (False, "FATAL_ERROR:TypeError:missing arg")
        tools = [
            {
                "name": "tool1",
                "entrypoint": "mod:func",
                "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
            }
        ]
        status, log = step_smoke(sandbox, tools)
        assert status == "failed"

    def test_smoke_caps_tools(self):
        """Smoke should only test up to VERIFICATION_SMOKE_MAX_TOOLS tools."""
        from app.verification.steps import step_smoke
        from unittest.mock import MagicMock, patch
        sandbox = MagicMock()
        sandbox.run_python_code.return_value = (True, "RESULT_TYPE:dict")

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
            status, log = step_smoke(sandbox, tools)

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


class TestExceptionCategories:
    """Verify that exception categories are properly split."""

    def test_value_error_not_in_acceptable(self):
        from app.verification.steps import ACCEPTABLE_EXCEPTIONS
        assert "ValueError" not in ACCEPTABLE_EXCEPTIONS

    def test_value_error_in_inconclusive(self):
        from app.verification.steps import INCONCLUSIVE_EXCEPTIONS
        assert "ValueError" in INCONCLUSIVE_EXCEPTIONS

    def test_key_error_in_inconclusive(self):
        from app.verification.steps import INCONCLUSIVE_EXCEPTIONS
        assert "KeyError" in INCONCLUSIVE_EXCEPTIONS

    def test_type_error_in_fatal(self):
        from app.verification.steps import FATAL_EXCEPTIONS
        assert "TypeError" in FATAL_EXCEPTIONS

    def test_file_not_found_in_acceptable(self):
        from app.verification.steps import ACCEPTABLE_EXCEPTIONS
        assert "FileNotFoundError" in ACCEPTABLE_EXCEPTIONS

    def test_no_overlap_between_categories(self):
        from app.verification.steps import ACCEPTABLE_EXCEPTIONS, INCONCLUSIVE_EXCEPTIONS, FATAL_EXCEPTIONS
        acc = set(ACCEPTABLE_EXCEPTIONS)
        inc = set(INCONCLUSIVE_EXCEPTIONS)
        fat = set(FATAL_EXCEPTIONS)
        assert acc & inc == set(), "Overlap between acceptable and inconclusive"
        assert acc & fat == set(), "Overlap between acceptable and fatal"
        assert inc & fat == set(), "Overlap between inconclusive and fatal"
