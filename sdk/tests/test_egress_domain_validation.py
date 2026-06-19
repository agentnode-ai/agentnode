"""Stage 2: fail-closed allowlist validation (no daemon)."""
from __future__ import annotations

import pytest

from agentnode_sdk.sandbox.egress import validate_allowed_domains as v


def test_empty_raises():
    with pytest.raises(ValueError):
        v([])


@pytest.mark.parametrize("bad", [
    "http://example.com",      # scheme
    "https://example.com/x",   # scheme + path
    "example.com/path",        # path
    "example.com:443",         # port
    "*.example.com",           # wildcard
    "user@example.com",        # userinfo
    "exa mple.com",            # whitespace
    "localhost",               # localhost
    "example",                 # single label
    "a..b",                    # empty label
    "-example.com",            # bad label
    "127.0.0.1",               # loopback IP
    "10.0.0.1",                # private IP
    "192.168.1.1",             # private IP
    "169.254.169.254",         # metadata IP
    "::1",                     # IPv6 loopback
])
def test_bad_domains_raise(bad):
    with pytest.raises(ValueError):
        v([bad])


def test_non_string_raises():
    with pytest.raises(ValueError):
        v([123])


def test_uppercase_normalized():
    assert v(["EXAMPLE.COM"]) == ("example.com",)


def test_trailing_dot_normalized():
    assert v(["example.com."]) == ("example.com",)


def test_valid_and_dedup_preserves_order():
    assert v(["api.example.com", "API.EXAMPLE.COM", "b.example.com"]) == \
        ("api.example.com", "b.example.com")
