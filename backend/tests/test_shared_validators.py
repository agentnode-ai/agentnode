"""Tests for shared validation helpers."""

import pytest

from app.shared.validators import (
    is_allowed_sort,
    is_safe_filter_value,
    is_safe_identifier,
    is_safe_url,
    is_valid_slug,
    normalize_tag,
    sanitize_to_identifier,
)


class TestSlugValidation:

    def test_valid_slug(self):
        assert is_valid_slug("pdf-reader-pack") is True
        assert is_valid_slug("my_tool_v2") is True
        assert is_valid_slug("a1b2c3") is True

    def test_too_short(self):
        assert is_valid_slug("ab") is False
        assert is_valid_slug("a") is False

    def test_starts_with_hyphen(self):
        assert is_valid_slug("-my-pack") is False

    def test_uppercase_rejected(self):
        assert is_valid_slug("My-Pack") is False

    def test_special_chars_rejected(self):
        assert is_valid_slug("my pack!") is False
        assert is_valid_slug('my"pack') is False


class TestUrlValidation:

    def test_https_accepted(self):
        assert is_safe_url("https://example.com") is True

    def test_http_accepted(self):
        assert is_safe_url("http://example.com") is True

    def test_javascript_rejected(self):
        assert is_safe_url("javascript:alert(1)") is False

    def test_data_rejected(self):
        assert is_safe_url("data:text/html,<h1>hi</h1>") is False

    def test_case_insensitive(self):
        assert is_safe_url("HTTPS://example.com") is True
        assert is_safe_url("JaVaScRiPt:alert(1)") is False

    def test_empty_rejected(self):
        assert is_safe_url("") is False


class TestFilterValidation:

    def test_normal_values(self):
        assert is_safe_filter_value("toolpack") is True
        assert is_safe_filter_value("python") is True
        assert is_safe_filter_value("my-publisher_1") is True

    def test_injection_rejected(self):
        assert is_safe_filter_value('evil" OR 1=1') is False
        assert is_safe_filter_value("value with spaces") is False
        assert is_safe_filter_value("val(ue)") is False
        assert is_safe_filter_value("val\\ue") is False

    def test_empty_rejected(self):
        assert is_safe_filter_value("") is False


class TestSortValidation:

    def test_allowed_sort(self):
        allowed = {"name:asc", "name:desc", "count:asc"}
        assert is_allowed_sort("name:asc", allowed) is True
        assert is_allowed_sort("count:asc", allowed) is True

    def test_disallowed_sort(self):
        allowed = {"name:asc"}
        assert is_allowed_sort("secret:asc", allowed) is False
        assert is_allowed_sort("name:asc, evil:desc", allowed) is False


class TestTagNormalization:

    def test_normal_tag(self):
        assert normalize_tag("pdf") == "pdf"
        assert normalize_tag("machine-learning") == "machine-learning"

    def test_uppercase_normalized(self):
        assert normalize_tag("PDF") == "pdf"

    def test_spaces_to_hyphens(self):
        assert normalize_tag("machine learning") == "machine-learning"

    def test_invalid_chars_rejected(self):
        assert normalize_tag("tag!@#") is None

    def test_too_short(self):
        assert normalize_tag("a") is None

    def test_single_valid_char(self):
        # Minimum 2 chars after normalization
        assert normalize_tag("ab") == "ab"


class TestIdentifierValidation:

    def test_valid_identifier(self):
        assert is_safe_identifier("my_func") is True
        assert is_safe_identifier("_private") is True

    def test_starts_with_digit(self):
        assert is_safe_identifier("1func") is False

    def test_special_chars(self):
        assert is_safe_identifier("my func") is False
        assert is_safe_identifier('my"func') is False

    def test_sanitize(self):
        assert sanitize_to_identifier("my-tool v2!") == "my_tool_v2_"
        assert sanitize_to_identifier("normal_name") == "normal_name"
        assert sanitize_to_identifier('evil"; import os; #') == "evil___import_os___"
