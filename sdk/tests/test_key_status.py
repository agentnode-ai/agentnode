"""Tests for online publisher key verification — TG-2."""
import json
from unittest.mock import patch, MagicMock

import pytest

from agentnode_sdk.key_status import (
    OnlineKeyStatus,
    KEY_STATUS_SEVERITY,
    KeyStatusResult,
    check_key_status,
)


# ---------------------------------------------------------------------------
# OnlineKeyStatus enum
# ---------------------------------------------------------------------------

class TestOnlineKeyStatus:
    def test_enum_values(self):
        assert OnlineKeyStatus.ACTIVE == "active"
        assert OnlineKeyStatus.REVOKED == "revoked"
        assert OnlineKeyStatus.UNKNOWN == "unknown"
        assert OnlineKeyStatus.MISMATCH == "mismatch"
        assert OnlineKeyStatus.ERROR == "error"

    def test_string_comparison(self):
        assert OnlineKeyStatus.ACTIVE == "active"
        assert OnlineKeyStatus.REVOKED == "revoked"

    def test_all_statuses_in_severity(self):
        for status in OnlineKeyStatus:
            assert status.value in KEY_STATUS_SEVERITY


# ---------------------------------------------------------------------------
# KEY_STATUS_SEVERITY
# ---------------------------------------------------------------------------

class TestKeyStatusSeverity:
    def test_mismatch_is_critical(self):
        assert KEY_STATUS_SEVERITY["mismatch"] == "critical"

    def test_revoked_is_high(self):
        assert KEY_STATUS_SEVERITY["revoked"] == "high"

    def test_unknown_is_medium(self):
        assert KEY_STATUS_SEVERITY["unknown"] == "medium"

    def test_error_is_availability(self):
        assert KEY_STATUS_SEVERITY["error"] == "availability"

    def test_active_is_none(self):
        assert KEY_STATUS_SEVERITY["active"] == "none"


# ---------------------------------------------------------------------------
# KeyStatusResult
# ---------------------------------------------------------------------------

class TestKeyStatusResult:
    def test_severity_active(self):
        r = KeyStatusResult(status=OnlineKeyStatus.ACTIVE, key_id="ed25519:abc")
        assert r.severity == "none"

    def test_severity_mismatch(self):
        r = KeyStatusResult(status=OnlineKeyStatus.MISMATCH, key_id="ed25519:abc")
        assert r.severity == "critical"

    def test_severity_revoked(self):
        r = KeyStatusResult(status=OnlineKeyStatus.REVOKED, key_id="ed25519:abc")
        assert r.severity == "high"

    def test_severity_unknown(self):
        r = KeyStatusResult(status=OnlineKeyStatus.UNKNOWN, key_id="ed25519:abc")
        assert r.severity == "medium"

    def test_severity_error(self):
        r = KeyStatusResult(status=OnlineKeyStatus.ERROR, key_id="ed25519:abc")
        assert r.severity == "availability"

    def test_fields(self):
        r = KeyStatusResult(
            status=OnlineKeyStatus.ACTIVE,
            key_id="ed25519:abc",
            publisher_slug="acme-ai",
            error=None,
        )
        assert r.status == OnlineKeyStatus.ACTIVE
        assert r.key_id == "ed25519:abc"
        assert r.publisher_slug == "acme-ai"
        assert r.error is None


# ---------------------------------------------------------------------------
# check_key_status — mock httpx
# ---------------------------------------------------------------------------

def _mock_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    return resp


class TestCheckKeyStatus:
    def test_active_key(self):
        resp = _mock_response(200, {
            "key_id": "ed25519:abc",
            "status": "active",
            "public_key": "AQID",
        })
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            result = check_key_status("acme-ai", "ed25519:abc", base_url="http://test")

        assert result.status == OnlineKeyStatus.ACTIVE
        assert result.key_id == "ed25519:abc"
        assert result.publisher_slug == "acme-ai"
        assert result.error is None

    def test_revoked_key(self):
        resp = _mock_response(200, {
            "key_id": "ed25519:abc",
            "status": "revoked",
        })
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            result = check_key_status("acme-ai", "ed25519:abc", base_url="http://test")

        assert result.status == OnlineKeyStatus.REVOKED
        assert result.error is None

    def test_404_unknown_key(self):
        resp = _mock_response(404)
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            result = check_key_status("acme-ai", "ed25519:abc", base_url="http://test")

        assert result.status == OnlineKeyStatus.UNKNOWN
        assert "not found" in result.error

    def test_network_error(self):
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.side_effect = ConnectionError("connection refused")
            result = check_key_status("acme-ai", "ed25519:abc", base_url="http://test")

        assert result.status == OnlineKeyStatus.ERROR
        assert "unreachable" in result.error.lower()

    def test_server_error(self):
        resp = _mock_response(500)
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            result = check_key_status("acme-ai", "ed25519:abc", base_url="http://test")

        assert result.status == OnlineKeyStatus.ERROR
        assert "500" in result.error

    def test_public_key_mismatch(self):
        resp = _mock_response(200, {
            "key_id": "ed25519:abc",
            "status": "active",
            "public_key": "REGISTRY_KEY",
        })
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            result = check_key_status(
                "acme-ai", "ed25519:abc", cached_public_key="LOCAL_KEY",
                base_url="http://test",
            )

        assert result.status == OnlineKeyStatus.MISMATCH
        assert result.severity == "critical"
        assert "does not match" in result.error

    def test_public_key_match(self):
        resp = _mock_response(200, {
            "key_id": "ed25519:abc",
            "status": "active",
            "public_key": "SAME_KEY",
        })
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            result = check_key_status(
                "acme-ai", "ed25519:abc", cached_public_key="SAME_KEY",
                base_url="http://test",
            )

        assert result.status == OnlineKeyStatus.ACTIVE

    def test_missing_public_key_in_response(self):
        resp = _mock_response(200, {
            "key_id": "ed25519:abc",
            "status": "active",
        })
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            result = check_key_status(
                "acme-ai", "ed25519:abc", cached_public_key="LOCAL_KEY",
                base_url="http://test",
            )

        assert result.status == OnlineKeyStatus.ACTIVE

    def test_no_cached_public_key(self):
        resp = _mock_response(200, {
            "key_id": "ed25519:abc",
            "status": "active",
            "public_key": "REGISTRY_KEY",
        })
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            result = check_key_status(
                "acme-ai", "ed25519:abc", cached_public_key=None,
                base_url="http://test",
            )

        assert result.status == OnlineKeyStatus.ACTIVE

    def test_unknown_registry_status(self):
        resp = _mock_response(200, {
            "key_id": "ed25519:abc",
            "status": "suspended",
        })
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            result = check_key_status("acme-ai", "ed25519:abc", base_url="http://test")

        assert result.status == OnlineKeyStatus.UNKNOWN
        assert "suspended" in result.error

    def test_invalid_json_response(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("invalid json")
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            result = check_key_status("acme-ai", "ed25519:abc", base_url="http://test")

        assert result.status == OnlineKeyStatus.ERROR
        assert "Invalid JSON" in result.error

    def test_url_construction(self):
        resp = _mock_response(200, {"status": "active"})
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            check_key_status(
                "acme-ai", "ed25519:abc",
                base_url="https://api.example.com",
            )
            url = mock_httpx.get.call_args[0][0]
            assert url == "https://api.example.com/v1/publishers/acme-ai/keys/ed25519:abc"

    def test_url_construction_with_v1(self):
        resp = _mock_response(200, {"status": "active"})
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            check_key_status(
                "acme-ai", "ed25519:abc",
                base_url="https://api.example.com/v1",
            )
            url = mock_httpx.get.call_args[0][0]
            assert url == "https://api.example.com/v1/publishers/acme-ai/keys/ed25519:abc"

    def test_api_key_header(self):
        resp = _mock_response(200, {"status": "active"})
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            check_key_status(
                "acme-ai", "ed25519:abc",
                base_url="http://test",
                api_key="test-key-123",
            )
            headers = mock_httpx.get.call_args[1]["headers"]
            assert headers["X-API-Key"] == "test-key-123"

    def test_no_api_key_no_header(self, monkeypatch):
        monkeypatch.delenv("AGENTNODE_API_KEY", raising=False)
        resp = _mock_response(200, {"status": "active"})
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            check_key_status(
                "acme-ai", "ed25519:abc",
                base_url="http://test",
            )
            headers = mock_httpx.get.call_args[1]["headers"]
            assert "X-API-Key" not in headers

    def test_timeout_parameter(self):
        resp = _mock_response(200, {"status": "active"})
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            check_key_status(
                "acme-ai", "ed25519:abc",
                base_url="http://test",
                timeout=5.0,
            )
            assert mock_httpx.get.call_args[1]["timeout"] == 5.0

    def test_401_error(self):
        resp = _mock_response(401)
        with patch("agentnode_sdk.key_status.httpx") as mock_httpx:
            mock_httpx.get.return_value = resp
            result = check_key_status("acme-ai", "ed25519:abc", base_url="http://test")

        assert result.status == OnlineKeyStatus.ERROR
        assert "401" in result.error
