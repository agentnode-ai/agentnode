"""Stage 3A: consent-identity + refusal scaffold (pure; no daemon, no secrets)."""
from __future__ import annotations

import pytest

from agentnode_sdk.runtimes.mcp_consent import (
    REASON_NO_DOMAINS,
    REASON_PENDING,
    ConsentIdentity,
    CredentialedMcpRefused,
    build_consent_identity,
    consent_key,
    redact_env_keys,
    refusal_reason,
)


def _id(**over):
    kw = dict(
        slug="gh-mcp", version="1.2.3", artifact_hash="sha256:abc",
        env_key_names=["GITHUB_TOKEN", "EXTRA"], allowed_domains=["api.github.com", "b.example.com"],
    )
    kw.update(over)
    return build_consent_identity(**kw)


# ---- identity normalization + stable key ----

def test_identity_normalizes_sorted_dedup():
    ident = build_consent_identity(
        "s", "1", "h",
        env_key_names=["B", "A", "A"], allowed_domains=["z.com", "a.com", "a.com"],
    )
    assert ident.env_key_names == ("A", "B")
    assert ident.allowed_domains == ("a.com", "z.com")


def test_consent_key_is_order_independent():
    a = build_consent_identity("s", "1", "h", ["A", "B"], ["x.com", "y.com"])
    b = build_consent_identity("s", "1", "h", ["B", "A"], ["y.com", "x.com"])
    assert consent_key(a) == consent_key(b)


def test_domains_are_canonicalized():
    ident = build_consent_identity(
        "s", "1", "h", ["A"], ["API.GITHUB.COM", "api.github.com.", " api.github.com "],
    )
    assert ident.allowed_domains == ("api.github.com",)


def test_consent_key_same_across_domain_write_variants():
    base = consent_key(build_consent_identity("s", "1", "h", ["A"], ["api.github.com"]))
    for variant in ("API.GITHUB.COM", "api.github.com.", "Api.GitHub.Com."):
        assert consent_key(build_consent_identity("s", "1", "h", ["A"], [variant])) == base


def test_env_key_names_stay_case_sensitive():
    # API_KEY and api_key are distinct env vars -> must NOT be merged
    ident = build_consent_identity("s", "1", "h", ["API_KEY", "api_key"], ["x.com"])
    assert ident.env_key_names == ("API_KEY", "api_key")
    a = consent_key(build_consent_identity("s", "1", "h", ["API_KEY"], ["x.com"]))
    b = consent_key(build_consent_identity("s", "1", "h", ["api_key"], ["x.com"]))
    assert a != b


@pytest.mark.parametrize("field,val", [
    ("slug", "other"),
    ("version", "9.9.9"),
    ("artifact_hash", "sha256:zzz"),
    ("env_key_names", ["GITHUB_TOKEN"]),
    ("allowed_domains", ["api.github.com"]),
])
def test_consent_key_changes_on_any_field(field, val):
    base = consent_key(_id())
    assert consent_key(_id(**{field: val})) != base


# ---- redaction: names + counts only, never values ----

def test_redact_names_and_count_only():
    out = redact_env_keys(["GITHUB_TOKEN", "SLACK_TOKEN"])
    assert out == "GITHUB_TOKEN, SLACK_TOKEN (2 keys)"


def test_redact_singular_and_empty():
    assert redact_env_keys(["ONLY"]) == "ONLY (1 key)"
    assert redact_env_keys([]) == "(none) (0 keys)"


def test_redact_never_contains_a_value():
    # caller passes NAMES; even if a value-looking string sneaks in, it is treated as a
    # name (there is no value channel) — assert no secret value is fabricated.
    out = redact_env_keys(["GITHUB_TOKEN"])
    assert "ghp_" not in out and "secret" not in out.lower()


# ---- refusal reasons: always a refusal, never 'allowed' ----

def test_refusal_reason_always_refuses():
    assert refusal_reason() == REASON_PENDING
    assert refusal_reason(allowed_domains_ok=True) == REASON_PENDING
    assert refusal_reason(allowed_domains_ok=False) == REASON_NO_DOMAINS
    # there is no input that yields '' / 'allowed'
    for ok in (True, False):
        assert refusal_reason(allowed_domains_ok=ok)
        assert refusal_reason(allowed_domains_ok=ok) not in ("", "allowed", "ok")


def test_exception_is_runtimeerror_with_reason():
    exc = CredentialedMcpRefused(REASON_PENDING, "human message")
    assert isinstance(exc, RuntimeError)
    assert exc.reason == REASON_PENDING
    assert "human message" in str(exc)


def test_no_allowed_symbol_exists():
    # Stage 3A invariant: the module exposes no allow/grant path.
    import agentnode_sdk.runtimes.mcp_consent as m
    assert not any(
        n.lower() in ("allow", "grant", "is_allowed", "approve") for n in dir(m)
    )
