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
    _check_method_action_consistency,
    _extract_tool_action_type,
    _measure_request_size,
    _measure_response_size,
    _check_scope_method_consistency,
    _is_read_only_scope,
    _MAX_REQUEST_BYTES,
    _MAX_RESPONSE_BYTES,
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
    """GAP-5 ADDRESSED (Phase 14.3): action_type vs HTTP method mismatch warned.

    Mismatches now produce warnings in logs and audit, but never block.
    Guard remains the policy authority.
    """

    @respx.mock
    def test_read_action_with_post_method_succeeds(self, tmp_path):
        """Mismatch warns but does NOT block execution."""
        respx.post("https://api.testapi.com/v1/actions").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry(tools=[
            {"name": "post_action", "endpoint": "/actions", "method": "POST",
             "action_type": "read"},
        ])
        result = run_remote("test-pack", "post_action", entry=entry)
        assert result.success is True

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_method_mismatch") is True
        assert any("read" in w and "POST" in w for w in r.get("remote_method_warnings", []))


class TestGAP2_ScopesNotEnforced:
    """GAP-2 ADDRESSED (Phase 14.5): Scope/method mismatches warned.

    Scopes are still not enforced — a read-only scope can still be used
    for DELETE requests. But obvious mismatches now produce warnings in
    logs and audit. Enforcement is deferred.
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


# ==========================================================================
# Phase 14.3 — Method/Action-Type Consistency Warnings
# ==========================================================================


class TestMethodActionConsistencyUnit:
    """Unit tests for _check_method_action_consistency helper."""

    def test_read_get_no_warning(self):
        assert _check_method_action_consistency("GET", "read") == []

    def test_read_post_warning(self):
        w = _check_method_action_consistency("POST", "read")
        assert len(w) == 1
        assert "read" in w[0] and "POST" in w[0]

    def test_read_delete_warning(self):
        w = _check_method_action_consistency("DELETE", "read")
        assert len(w) == 1

    def test_delete_get_warning(self):
        w = _check_method_action_consistency("GET", "delete")
        assert len(w) == 1
        assert "delete" in w[0] and "GET" in w[0]

    def test_delete_delete_no_warning(self):
        assert _check_method_action_consistency("DELETE", "delete") == []

    def test_missing_action_type_delete_warning(self):
        w = _check_method_action_consistency("DELETE", None)
        assert len(w) == 1
        assert "no action_type" in w[0]

    def test_missing_action_type_put_warning(self):
        w = _check_method_action_consistency("PUT", None)
        assert len(w) == 1

    def test_missing_action_type_get_no_warning(self):
        assert _check_method_action_consistency("GET", None) == []

    def test_missing_action_type_post_no_warning(self):
        assert _check_method_action_consistency("POST", None) == []

    def test_execute_post_no_warning(self):
        assert _check_method_action_consistency("POST", "execute") == []

    def test_write_external_get_no_warning(self):
        assert _check_method_action_consistency("GET", "write_external") == []

    def test_write_post_no_warning(self):
        assert _check_method_action_consistency("POST", "write") == []


class TestExtractToolActionType:
    """Unit tests for _extract_tool_action_type helper."""

    def test_extracts_action_type(self):
        entry = {"tools": [{"name": "my_tool", "action_type": "read"}]}
        assert _extract_tool_action_type("my_tool", entry) == "read"

    def test_returns_none_when_missing(self):
        entry = {"tools": [{"name": "my_tool"}]}
        assert _extract_tool_action_type("my_tool", entry) is None

    def test_returns_none_for_unknown_tool(self):
        entry = {"tools": [{"name": "other_tool", "action_type": "read"}]}
        assert _extract_tool_action_type("my_tool", entry) is None

    def test_returns_none_for_no_tool_name(self):
        entry = {"tools": [{"name": "my_tool", "action_type": "read"}]}
        assert _extract_tool_action_type(None, entry) is None


class TestMethodActionConsistencyIntegration:
    """Integration: warnings appear in audit and do not block execution."""

    @respx.mock
    def test_read_post_warning_in_audit(self, tmp_path):
        respx.post("https://api.testapi.com/v1/actions").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry(tools=[
            {"name": "post_action", "endpoint": "/actions", "method": "POST",
             "action_type": "read"},
        ])
        result = run_remote("test-pack", "post_action", entry=entry)
        assert result.success is True

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_method_mismatch") is True
        assert any("read" in w and "POST" in w for w in r["remote_method_warnings"])

    @respx.mock
    def test_read_get_no_mismatch_in_audit(self, tmp_path):
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry(tools=[
            {"name": "get_data", "endpoint": "/data", "method": "GET",
             "action_type": "read"},
        ])
        run_remote("test-pack", "get_data", entry=entry)

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_method_mismatch") is None
        assert "remote_method_warnings" not in r

    @respx.mock
    def test_delete_get_warning_in_audit(self, tmp_path):
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry(tools=[
            {"name": "get_data", "endpoint": "/data", "method": "GET",
             "action_type": "delete"},
        ])
        result = run_remote("test-pack", "get_data", entry=entry)
        assert result.success is True

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_method_mismatch") is True

    @respx.mock
    def test_missing_action_type_delete_warning_in_audit(self, tmp_path):
        respx.delete("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry(tools=[
            {"name": "get_data", "endpoint": "/data", "method": "DELETE"},
        ])
        result = run_remote("test-pack", "get_data", entry=entry)
        assert result.success is True

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_method_mismatch") is True
        assert any("no action_type" in w for w in r["remote_method_warnings"])

    @respx.mock
    def test_warning_does_not_block_on_failure(self, tmp_path):
        """Mismatch warning still recorded even on HTTP error."""
        respx.post("https://api.testapi.com/v1/actions").mock(
            return_value=httpx.Response(403, json={"error": "forbidden"})
        )
        entry = _entry(tools=[
            {"name": "post_action", "endpoint": "/actions", "method": "POST",
             "action_type": "read"},
        ])
        result = run_remote("test-pack", "post_action", entry=entry)
        assert result.success is False

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_method_mismatch") is True


# ==========================================================================
# Phase 14.4 — Request/Response Size Limits (warn-only)
# ==========================================================================


class TestMeasureRequestSizeUnit:
    """Unit tests for _measure_request_size helper."""

    def test_empty_kwargs_returns_zero(self):
        size, unknown = _measure_request_size({})
        assert size == 0
        assert unknown is False

    def test_small_kwargs(self):
        size, unknown = _measure_request_size({"key": "value"})
        assert size is not None
        assert size > 0
        assert unknown is False

    def test_none_kwargs(self):
        size, unknown = _measure_request_size(None)
        assert size == 0
        assert unknown is False


class TestMeasureResponseSizeUnit:
    """Unit tests for _measure_response_size helper."""

    def test_empty_body(self):
        from agentnode_sdk.credential_handle import AuthorizedResponse
        resp = AuthorizedResponse(status_code=200, headers={}, body="")
        assert _measure_response_size(resp) == 0

    def test_normal_body(self):
        from agentnode_sdk.credential_handle import AuthorizedResponse
        resp = AuthorizedResponse(status_code=200, headers={}, body='{"ok": true}')
        assert _measure_response_size(resp) == len('{"ok": true}'.encode("utf-8"))

    def test_unicode_body(self):
        from agentnode_sdk.credential_handle import AuthorizedResponse
        body = "Héllo wörld"
        resp = AuthorizedResponse(status_code=200, headers={}, body=body)
        assert _measure_response_size(resp) == len(body.encode("utf-8"))


class TestRequestSizeWarningIntegration:
    """Integration: oversized request produces warning in audit."""

    @respx.mock
    def test_small_request_no_warning(self, tmp_path):
        respx.post("https://api.testapi.com/v1/actions").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry()
        result = run_remote("test-pack", "post_action", entry=entry, key="value")
        assert result.success is True

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_request_size_warning") is None
        assert "remote_request_size_bytes" not in r

    @respx.mock
    def test_oversized_request_warning(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "agentnode_sdk.runtimes.remote_runner._MAX_REQUEST_BYTES", 50,
        )
        respx.post("https://api.testapi.com/v1/actions").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry()
        big_data = "x" * 200
        result = run_remote("test-pack", "post_action", entry=entry, data=big_data)
        assert result.success is True

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_request_size_warning") is True
        assert r["remote_request_size_bytes"] > 50
        assert r["remote_request_size_limit"] == 50

    @respx.mock
    def test_oversized_request_does_not_block(self, tmp_path, monkeypatch):
        """Size warning never blocks — request still executes."""
        monkeypatch.setattr(
            "agentnode_sdk.runtimes.remote_runner._MAX_REQUEST_BYTES", 10,
        )
        respx.post("https://api.testapi.com/v1/actions").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry()
        result = run_remote("test-pack", "post_action", entry=entry, data="x" * 100)
        assert result.success is True


class TestResponseSizeWarningIntegration:
    """Integration: oversized response produces warning in audit."""

    @respx.mock
    def test_small_response_no_warning(self, tmp_path):
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry()
        result = run_remote("test-pack", "get_data", entry=entry)
        assert result.success is True

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_response_size_warning") is None

    @respx.mock
    def test_oversized_response_warning(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "agentnode_sdk.runtimes.remote_runner._MAX_RESPONSE_BYTES", 50,
        )
        big_body = "x" * 200
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, text=big_body)
        )
        entry = _entry()
        result = run_remote("test-pack", "get_data", entry=entry)
        assert result.success is True

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_response_size_warning") is True
        assert r["remote_response_size_bytes"] > 50
        assert r["remote_response_size_limit"] == 50

    @respx.mock
    def test_oversized_response_does_not_block(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "agentnode_sdk.runtimes.remote_runner._MAX_RESPONSE_BYTES", 10,
        )
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, text="x" * 100)
        )
        entry = _entry()
        result = run_remote("test-pack", "get_data", entry=entry)
        assert result.success is True

    @respx.mock
    def test_oversized_error_response_warning(self, tmp_path, monkeypatch):
        """Size warning works on error responses too."""
        monkeypatch.setattr(
            "agentnode_sdk.runtimes.remote_runner._MAX_RESPONSE_BYTES", 50,
        )
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(403, text="x" * 200)
        )
        entry = _entry()
        result = run_remote("test-pack", "get_data", entry=entry)
        assert result.success is False

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_response_size_warning") is True


class TestSizeAuditSafety:
    """Audit must contain size numbers but never payloads."""

    @respx.mock
    def test_no_payload_in_audit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "agentnode_sdk.runtimes.remote_runner._MAX_REQUEST_BYTES", 10,
        )
        monkeypatch.setattr(
            "agentnode_sdk.runtimes.remote_runner._MAX_RESPONSE_BYTES", 10,
        )
        secret_payload = "supersecret_payload_data_1234"
        respx.post("https://api.testapi.com/v1/actions").mock(
            return_value=httpx.Response(200, text="response_body_secret_5678")
        )
        entry = _entry()
        run_remote("test-pack", "post_action", entry=entry, data=secret_payload)

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        raw = json.dumps(remote[-1])
        assert "supersecret_payload_data" not in raw
        assert "response_body_secret" not in raw


# ==========================================================================
# Phase 14.5 — Scope/Method Mismatch Logging
# ==========================================================================


class TestIsReadOnlyScope:
    """Unit tests for _is_read_only_scope heuristic."""

    def test_read(self):
        assert _is_read_only_scope("read") is True

    def test_readonly(self):
        assert _is_read_only_scope("readonly") is True

    def test_view(self):
        assert _is_read_only_scope("view") is True

    def test_list(self):
        assert _is_read_only_scope("list") is True

    def test_dotted_read(self):
        assert _is_read_only_scope("users.read") is True

    def test_colon_read(self):
        assert _is_read_only_scope("channels:read") is True

    def test_write_not_read(self):
        assert _is_read_only_scope("write") is False

    def test_send_not_read(self):
        assert _is_read_only_scope("email.send") is False

    def test_delete_not_read(self):
        assert _is_read_only_scope("delete") is False

    def test_admin_not_read(self):
        assert _is_read_only_scope("admin") is False

    def test_create_not_read(self):
        assert _is_read_only_scope("create") is False

    def test_case_insensitive(self):
        assert _is_read_only_scope("READ") is True
        assert _is_read_only_scope("Users.Read") is True


class TestScopeMethodConsistencyUnit:
    """Unit tests for _check_scope_method_consistency helper."""

    def test_read_scope_get_no_warning(self):
        assert _check_scope_method_consistency("GET", ["read"]) == []

    def test_read_scope_post_warning(self):
        w = _check_scope_method_consistency("POST", ["read"])
        assert len(w) == 1
        assert "read-only" in w[0]

    def test_users_read_delete_warning(self):
        w = _check_scope_method_consistency("DELETE", ["users.read"])
        assert len(w) == 1

    def test_email_send_post_no_warning(self):
        assert _check_scope_method_consistency("POST", ["email.send"]) == []

    def test_write_put_no_warning(self):
        assert _check_scope_method_consistency("PUT", ["write"]) == []

    def test_delete_scope_delete_no_warning(self):
        assert _check_scope_method_consistency("DELETE", ["delete"]) == []

    def test_admin_scope_delete_no_warning(self):
        assert _check_scope_method_consistency("DELETE", ["admin"]) == []

    def test_mixed_read_write_post_no_warning(self):
        assert _check_scope_method_consistency("POST", ["read", "write"]) == []

    def test_empty_scopes_post_no_warning(self):
        assert _check_scope_method_consistency("POST", []) == []

    def test_multiple_read_scopes_post_warning(self):
        w = _check_scope_method_consistency("POST", ["channels:read", "users.read"])
        assert len(w) == 1

    def test_read_scope_head_no_warning(self):
        assert _check_scope_method_consistency("HEAD", ["read"]) == []

    def test_read_scope_options_no_warning(self):
        assert _check_scope_method_consistency("OPTIONS", ["read"]) == []


class TestScopeMethodConsistencyIntegration:
    """Integration: scope/method warnings in audit."""

    @respx.mock
    def test_read_scope_post_warning_in_audit(self, tmp_path):
        respx.post("https://api.testapi.com/v1/actions").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry(
            tools=[{"name": "post_action", "endpoint": "/actions", "method": "POST"}],
            connector={"provider": "testapi", "auth_type": "api_key", "scopes": ["read"]},
        )
        result = run_remote("test-pack", "post_action", entry=entry)
        assert result.success is True

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_scope_method_mismatch") is True
        assert any("read-only" in w for w in r["remote_scope_method_warnings"])

    @respx.mock
    def test_read_scope_get_no_mismatch_in_audit(self, tmp_path):
        respx.get("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry(
            tools=[{"name": "get_data", "endpoint": "/data", "method": "GET"}],
            connector={"provider": "testapi", "auth_type": "api_key", "scopes": ["read"]},
        )
        run_remote("test-pack", "get_data", entry=entry)

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_scope_method_mismatch") is None

    @respx.mock
    def test_write_scope_post_no_mismatch(self, tmp_path):
        respx.post("https://api.testapi.com/v1/actions").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry(
            tools=[{"name": "post_action", "endpoint": "/actions", "method": "POST"}],
            connector={"provider": "testapi", "auth_type": "api_key", "scopes": ["write"]},
        )
        run_remote("test-pack", "post_action", entry=entry)

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_scope_method_mismatch") is None

    @respx.mock
    def test_empty_scopes_no_warning(self, tmp_path):
        respx.post("https://api.testapi.com/v1/actions").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry(
            tools=[{"name": "post_action", "endpoint": "/actions", "method": "POST"}],
            connector={"provider": "testapi", "auth_type": "api_key", "scopes": []},
        )
        run_remote("test-pack", "post_action", entry=entry)

        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        r = remote[-1]
        assert r.get("remote_scope_method_mismatch") is None

    @respx.mock
    def test_scope_warning_does_not_block(self, tmp_path):
        """Scope warning never blocks execution."""
        respx.delete("https://api.testapi.com/v1/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        entry = _entry(
            tools=[{"name": "get_data", "endpoint": "/data", "method": "DELETE"}],
            connector={"provider": "testapi", "auth_type": "api_key", "scopes": ["channels:read"]},
        )
        result = run_remote("test-pack", "get_data", entry=entry)
        assert result.success is True
        entries = _read_audit(tmp_path)
        remote = [e for e in entries if e["event"] == "remote_run"]
        assert remote[-1].get("remote_scope_method_mismatch") is True
