"""Phase 14.1 — Remote runtime characterization tests + audit enhancement.

Documents current-state behavior for known gaps (GAP-1 through GAP-5).
Tests marked with GAP-N confirm the *current* behavior, not the desired target.
Audit enhancement tests verify new remote_* prefixed fields.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
import respx

from agentnode_sdk.credential_handle import CredentialHandle
from agentnode_sdk.runtimes.remote_runner import (
    run_remote,
    _audit_remote_call,
    _extract_allowed_domains,
)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".agentnode"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    cfg_file.write_text(json.dumps({
        "version": "0.1",
        "trust": {"minimum_trust_level": "unverified"},
        "permissions": {
            "network": "allow",
            "filesystem": "allow",
            "code_execution": "allow",
        },
    }))
    monkeypatch.setenv("AGENTNODE_CONFIG", str(cfg_file))
    monkeypatch.setenv("AGENTNODE_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("AGENTNODE_CRED_TESTAPI", "test-secret-key")
    yield tmp_path


def _entry(**overrides):
    base = {
        "version": "1.0.0",
        "package_type": "toolpack",
        "runtime": "remote",
        "remote_endpoint": "https://api.testapi.com/v1",
        "entrypoint": "",
        "tools": [
            {"name": "get_data", "endpoint": "/data", "method": "GET"},
            {"name": "post_action", "endpoint": "/actions", "method": "POST"},
        ],
        "connector": {
            "provider": "testapi",
            "auth_type": "api_key",
            "scopes": ["read", "write"],
        },
        "trust_level": "verified",
    }
    base.update(overrides)
    return base


def _read_audit(tmp_path: Path) -> list[dict]:
    audit_path = tmp_path / ".agentnode" / "audit.jsonl"
    if not audit_path.exists():
        return []
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


# ==========================================================================
# Current-state characterization: document known gaps
# ==========================================================================


class TestGAP1_EmptyAllowedDomains:
    """GAP-1 CLOSED (Phase 14.2): Empty allowed_domains = deny.

    Credentialed handles without domain binding refuse all requests.
    """

    def test_empty_allowed_domains_denies_any_host(self):
        handle = CredentialHandle(
            provider="test",
            auth_type="api_key",
            scopes=[],
            allowed_domains=[],
            secret_data={"api_key": "secret"},
        )
        assert handle.is_domain_allowed("https://evil.com/steal") is False
        assert handle.is_domain_allowed("https://any.host.com") is False

    def test_empty_allowed_domains_raises_on_request(self):
        handle = CredentialHandle(
            provider="test",
            auth_type="api_key",
            scopes=[],
            allowed_domains=[],
            secret_data={"api_key": "secret"},
        )
        with pytest.raises(PermissionError, match="no allowed_domains"):
            handle.authorized_request("GET", "https://any.host.com/data")

    def test_extract_returns_empty_for_bad_endpoint(self):
        domains = _extract_allowed_domains("not-a-url", {})
        assert isinstance(domains, list)


class TestGAP4_HttpProtocol:
    """GAP-4 CLOSED (Phase 14.2): Credentialed HTTP requests denied.

    is_domain_allowed() still checks hostname only (protocol-agnostic).
    _require_secure_target() enforces HTTPS before any credentialed request.
    """

    def test_http_url_passes_domain_check(self):
        """is_domain_allowed is hostname-only — http:// matches by design."""
        handle = CredentialHandle(
            provider="test",
            auth_type="api_key",
            scopes=[],
            allowed_domains=["api.example.com"],
            secret_data={"api_key": "secret"},
        )
        assert handle.is_domain_allowed("http://api.example.com/data") is True

    def test_https_url_passes_domain_check(self):
        handle = CredentialHandle(
            provider="test",
            auth_type="api_key",
            scopes=[],
            allowed_domains=["api.example.com"],
            secret_data={"api_key": "secret"},
        )
        assert handle.is_domain_allowed("https://api.example.com/data") is True

    def test_http_credential_request_denied(self):
        """Credentialed request over HTTP raises before sending."""
        handle = CredentialHandle(
            provider="test",
            auth_type="api_key",
            scopes=[],
            allowed_domains=["api.example.com"],
            secret_data={"api_key": "secret"},
        )
        with pytest.raises(PermissionError, match="requires HTTPS"):
            handle.authorized_request("GET", "http://api.example.com/data")

    def test_http_credential_headers_denied(self):
        """authorized_request_headers() also enforces HTTPS."""
        handle = CredentialHandle(
            provider="test",
            auth_type="api_key",
            scopes=[],
            allowed_domains=["api.example.com"],
            secret_data={"api_key": "secret"},
        )
        with pytest.raises(PermissionError, match="requires HTTPS"):
            handle.authorized_request_headers("http://api.example.com/data")


class TestDomainMatchBehavior:
    """Exact hostname matching — correct, secure behavior."""

    def test_exact_match_required(self):
        handle = CredentialHandle(
            provider="test",
            auth_type="api_key",
            scopes=[],
            allowed_domains=["api.slack.com"],
            secret_data={"api_key": "secret"},
        )
        assert handle.is_domain_allowed("https://api.slack.com/data") is True
        assert handle.is_domain_allowed("https://sub.api.slack.com/data") is False
        assert handle.is_domain_allowed("https://slack.com/data") is False

    def test_case_insensitive(self):
        handle = CredentialHandle(
            provider="test",
            auth_type="api_key",
            scopes=[],
            allowed_domains=["API.Slack.Com"],
            secret_data={"api_key": "secret"},
        )
        assert handle.is_domain_allowed("https://api.slack.com/data") is True

    def test_bad_url_returns_false(self):
        handle = CredentialHandle(
            provider="test",
            auth_type="api_key",
            scopes=[],
            allowed_domains=["api.slack.com"],
            secret_data={"api_key": "secret"},
        )
        assert handle.is_domain_allowed("not-a-url") is False


class TestRedirectBehavior:
    """httpx defaults to follow_redirects=False — safe behavior.

    A 302 redirect is not followed. The raw 302 response is returned as-is.
    Since 302 < 400, run_remote treats it as success with empty body.
    The credential is NOT sent to the redirect target.
    """

    @respx.mock
    def test_redirects_not_followed(self):
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(
                302,
                headers={"Location": "https://evil.com/steal"},
            )
        )
        entry = _entry()
        result = run_remote("test-pack", "get_data", entry=entry)
        # 302 < 400 → treated as success, redirect NOT followed
        assert result.success is True
        # No request made to evil.com
        assert "evil.com" not in str(result.result)


class TestGAP5_ActionTypeMethodMismatch:
    """GAP-5: action_type vs HTTP method mismatch not detected.

    Current behavior: a tool declaring action_type=read can use method=POST
    without any warning or audit entry. Guard trusts the manifest classification.
    Phase 14.3 will add warnings for mismatches.
    """

    @respx.mock
    def test_read_action_with_post_method_succeeds(self):
        respx.post("https://api.testapi.com/v1/actions").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry(tools=[
            {"name": "post_action", "endpoint": "/actions", "method": "POST",
             "action_type": "read"},
        ])
        result = run_remote("test-pack", "post_action", entry=entry)
        assert result.success is True


class TestGAP2_ScopesNotEnforced:
    """GAP-2: Scopes are stored but never enforced at runtime.

    Current behavior: a credential with scopes=["read"] can be used for
    any HTTP method (POST, DELETE, etc.) without restriction.
    Phase 14.5 will add scope-method mismatch logging.
    """

    def test_scopes_stored_on_handle(self):
        handle = CredentialHandle(
            provider="test",
            auth_type="api_key",
            scopes=["read"],
            allowed_domains=["api.example.com"],
            secret_data={"api_key": "secret"},
        )
        assert handle.scopes == ["read"]

    @respx.mock
    def test_read_scope_allows_delete_method(self):
        respx.delete("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, json={"deleted": True})
        )
        entry = _entry(
            tools=[{"name": "get_data", "endpoint": "/data", "method": "DELETE"}],
            connector={
                "provider": "testapi",
                "auth_type": "api_key",
                "scopes": ["read"],
            },
        )
        result = run_remote("test-pack", "get_data", entry=entry)
        assert result.success is True


# ==========================================================================
# Audit enhancement: verify remote_* prefixed fields
# ==========================================================================


class TestRemoteAuditFields:
    """remote_run audit events include safe metadata with remote_* prefix."""

    @respx.mock
    def test_success_audit_has_remote_fields(self, tmp_path):
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry()
        result = run_remote("test-pack", "get_data", entry=entry)
        assert result.success

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        assert len(remote) >= 1
        r = remote[-1]
        assert r["remote_method"] == "GET"
        assert r["remote_domain"] == "api.testapi.com"
        assert r["remote_status_code"] == 200
        assert isinstance(r["remote_duration_ms"], int)
        assert r["remote_duration_ms"] >= 0
        assert r["remote_provider"] == "testapi"

    @respx.mock
    def test_failure_audit_has_remote_fields(self, tmp_path):
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(403, json={"error": "forbidden"})
        )
        entry = _entry()
        result = run_remote("test-pack", "get_data", entry=entry)
        assert not result.success

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        assert len(remote) >= 1
        r = remote[-1]
        assert r["remote_method"] == "GET"
        assert r["remote_domain"] == "api.testapi.com"
        assert r["remote_status_code"] == 403
        assert r["remote_provider"] == "testapi"
        assert r["action"] == "deny"

    @respx.mock
    def test_audit_reason_includes_status(self, tmp_path):
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry()
        run_remote("test-pack", "get_data", entry=entry)

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert "200" in r["reason"]

    @respx.mock
    def test_post_method_audited(self, tmp_path):
        respx.post("https://api.testapi.com/v1/actions").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry()
        run_remote("test-pack", "post_action", entry=entry)

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r["remote_method"] == "POST"


class TestRemoteAuditSafety:
    """Audit entries must not contain URLs, paths, kwargs, bodies, or secrets."""

    @respx.mock
    def test_no_url_path_in_audit(self, tmp_path):
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry()
        run_remote("test-pack", "get_data", entry=entry, secret_param="hunter2")

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        raw = json.dumps(remote[-1])
        assert "/v1/data" not in raw
        assert "/data" not in raw
        assert "hunter2" not in raw
        assert "secret_param" not in raw

    @respx.mock
    def test_no_request_body_in_audit(self, tmp_path):
        respx.post("https://api.testapi.com/v1/actions").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry()
        run_remote(
            "test-pack", "post_action", entry=entry,
            password="supersecret", data={"key": "value"},
        )

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        raw = json.dumps(remote[-1])
        assert "supersecret" not in raw
        assert "password" not in raw

    @respx.mock
    def test_no_auth_headers_in_audit(self, tmp_path):
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry()
        run_remote("test-pack", "get_data", entry=entry)

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        raw = json.dumps(remote[-1])
        assert "test-secret-key" not in raw
        assert "Bearer" not in raw
        assert "Authorization" not in raw

    def test_audit_helper_never_crashes(self, tmp_path):
        from unittest import mock
        with mock.patch(
            "agentnode_sdk.runtimes.remote_runner.audit_decision",
            side_effect=OSError("disk full"),
        ):
            _audit_remote_call(
                "test-pack", "get_data", "testapi",
                method="GET",
                domain="api.testapi.com",
                status_code=200,
                duration_ms=100.0,
                success=True,
            )


class TestRemoteAuditDomainOnly:
    """Verify audit contains hostname only, never full URL."""

    @respx.mock
    def test_domain_is_hostname_only(self, tmp_path):
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry()
        run_remote("test-pack", "get_data", entry=entry)

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r["remote_domain"] == "api.testapi.com"
        assert "://" not in r["remote_domain"]
        assert "/" not in r["remote_domain"]
