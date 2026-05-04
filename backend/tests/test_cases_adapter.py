"""Tests for verification.cases_adapter — format normalization."""

import pytest

from app.verification.cases_adapter import (
    NormalizedVerification,
    normalize_verification_config,
)


class TestNormalizedVerification:
    def test_defaults(self):
        nv = NormalizedVerification()
        assert nv.cases == []
        assert nv.system_requirements == []
        assert nv.source_format == "none"
        assert nv.has_explicit_cases is False


class TestNormalizeNone:
    def test_none_input(self):
        result = normalize_verification_config(None)
        assert result.has_explicit_cases is False
        assert result.source_format == "none"
        assert result.cases == []

    def test_empty_dict(self):
        result = normalize_verification_config({})
        assert result.has_explicit_cases is False
        assert result.source_format == "none"

    def test_non_dict(self):
        result = normalize_verification_config("garbage")
        assert result.has_explicit_cases is False


class TestNormalizeCases:
    def test_basic_cases(self):
        config = {
            "cases": [
                {
                    "name": "basic_search",
                    "tool": "search_web",
                    "input": {"query": "test"},
                    "cassette": "fixtures/cassettes/search.yaml",
                    "expected": {"return_type": "dict", "required_keys": ["results"]},
                }
            ],
            "system_requirements": ["browser"],
        }
        result = normalize_verification_config(config)
        assert result.has_explicit_cases is True
        assert result.source_format == "cases"
        assert len(result.cases) == 1
        assert result.cases[0]["name"] == "basic_search"
        assert result.cases[0]["input"] == {"query": "test"}
        assert result.cases[0]["cassette"] == "fixtures/cassettes/search.yaml"
        assert result.cases[0]["tool"] == "search_web"
        assert result.cases[0]["mode"] == "fixture"
        assert result.system_requirements == ["browser"]

    def test_cases_without_optional_fields(self):
        config = {
            "cases": [{"name": "minimal", "input": {"x": 1}}]
        }
        result = normalize_verification_config(config)
        assert result.has_explicit_cases is True
        assert result.cases[0]["tool"] is None
        assert result.cases[0]["cassette"] is None
        assert result.cases[0]["expected"] is None
        assert result.cases[0]["mode"] == "real"

    def test_cases_empty_list_falls_through(self):
        config = {"cases": [], "test_input": {"x": 1}}
        result = normalize_verification_config(config)
        assert result.source_format == "test_input"

    def test_multiple_cases(self):
        config = {
            "cases": [
                {"name": "case1", "input": {"a": 1}},
                {"name": "case2", "input": {"b": 2}, "cassette": "c.yaml"},
            ]
        }
        result = normalize_verification_config(config)
        assert len(result.cases) == 2
        assert result.cases[0]["cassette"] is None
        assert result.cases[0]["mode"] == "real"
        assert result.cases[1]["cassette"] == "c.yaml"
        assert result.cases[1]["mode"] == "fixture"


class TestNormalizeFixtures:
    def test_basic_fixture(self):
        config = {
            "fixtures": [
                {
                    "name": "transcribe_audio_api",
                    "tool": "document_parsing",
                    "test_input": {"audio_path": "/workspace/fixtures/test.wav", "api_key": "sk-test"},
                    "cassette": "fixtures/cassettes/transcribe_api.yaml",
                    "expected": {"return_type": "dict", "required_keys": ["text"]},
                }
            ]
        }
        result = normalize_verification_config(config)
        assert result.has_explicit_cases is True
        assert result.source_format == "fixtures"
        assert len(result.cases) == 1
        assert result.cases[0]["name"] == "transcribe_audio_api"
        assert result.cases[0]["input"] == {"audio_path": "/workspace/fixtures/test.wav", "api_key": "sk-test"}
        assert result.cases[0]["cassette"] == "fixtures/cassettes/transcribe_api.yaml"
        assert result.cases[0]["tool"] == "document_parsing"

    def test_fixture_without_test_input(self):
        config = {"fixtures": [{"name": "bare", "cassette": "c.yaml"}]}
        result = normalize_verification_config(config)
        assert result.cases[0]["input"] == {}

    def test_fixtures_priority_over_test_input(self):
        config = {
            "fixtures": [{"name": "f1", "test_input": {"a": 1}, "cassette": "c.yaml"}],
            "test_input": {"b": 2},
        }
        result = normalize_verification_config(config)
        assert result.source_format == "fixtures"
        assert result.cases[0]["input"] == {"a": 1}


class TestNormalizeTestInput:
    def test_basic_test_input(self):
        config = {"test_input": {"image_path": "/tmp/test.png", "api_key": "sk-test"}}
        result = normalize_verification_config(config)
        assert result.has_explicit_cases is True
        assert result.source_format == "test_input"
        assert len(result.cases) == 1
        assert result.cases[0]["name"] == "legacy_test_input"
        assert result.cases[0]["input"] == {"image_path": "/tmp/test.png", "api_key": "sk-test"}
        assert result.cases[0]["cassette"] is None
        assert result.cases[0]["mode"] == "real"

    def test_empty_test_input_is_none(self):
        config = {"test_input": {}}
        result = normalize_verification_config(config)
        assert result.has_explicit_cases is False
        assert result.source_format == "none"


class TestSystemRequirements:
    def test_preserved_with_cases(self):
        config = {
            "cases": [{"name": "x", "input": {"a": 1}}],
            "system_requirements": ["browser", "ffmpeg"],
        }
        result = normalize_verification_config(config)
        assert result.system_requirements == ["browser", "ffmpeg"]

    def test_preserved_with_fixtures(self):
        config = {
            "fixtures": [{"name": "x", "test_input": {"a": 1}, "cassette": "c.yaml"}],
            "system_requirements": ["tesseract"],
        }
        result = normalize_verification_config(config)
        assert result.system_requirements == ["tesseract"]

    def test_invalid_system_requirements_ignored(self):
        config = {
            "cases": [{"name": "x", "input": {"a": 1}}],
            "system_requirements": "not-a-list",
        }
        result = normalize_verification_config(config)
        assert result.system_requirements == []

    def test_empty_config_no_system_requirements(self):
        result = normalize_verification_config({})
        assert result.system_requirements == []


class TestPriorityOrder:
    def test_cases_wins_over_fixtures_and_test_input(self):
        config = {
            "cases": [{"name": "c1", "input": {"x": 1}}],
            "fixtures": [{"name": "f1", "test_input": {"y": 2}, "cassette": "c.yaml"}],
            "test_input": {"z": 3},
        }
        result = normalize_verification_config(config)
        assert result.source_format == "cases"
        assert result.cases[0]["input"] == {"x": 1}

    def test_fixtures_wins_over_test_input(self):
        config = {
            "fixtures": [{"name": "f1", "test_input": {"y": 2}, "cassette": "c.yaml"}],
            "test_input": {"z": 3},
        }
        result = normalize_verification_config(config)
        assert result.source_format == "fixtures"


class TestCaseSplitting:
    """Verify downstream consumers can split fixture vs real cases."""

    def test_split_by_mode(self):
        config = {
            "cases": [
                {"name": "fixture_case", "input": {"a": 1}, "cassette": "c.yaml"},
                {"name": "real_case", "input": {"b": 2}},
            ]
        }
        result = normalize_verification_config(config)
        fixture_cases = [c for c in result.cases if c["mode"] == "fixture"]
        real_cases = [c for c in result.cases if c["mode"] == "real"]
        assert len(fixture_cases) == 1
        assert len(real_cases) == 1
        assert fixture_cases[0]["name"] == "fixture_case"
        assert real_cases[0]["name"] == "real_case"
