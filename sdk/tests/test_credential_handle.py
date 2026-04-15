"""Tests for CredentialHandle — security invariants S3, S8, S12."""
import pickle

import httpx
import pytest
import respx

from agentnode_sdk.credential_handle import AuthorizedResponse, CredentialHandle


def _make_handle(**overrides):
    defaults = {
        "provider": "slack",
        "auth_type": "oauth2",
        "scopes": ["channels:read"],
        "allowed_domains": ["slack.com", "api.slack.com"],
        "secret_data": {"access_token": "xoxb-secret-token"},
    }
    defaults.update(overrides)
    return CredentialHandle(**defaults)


class TestCredentialHandleSecurity:
    """S12: Secrets never exposed via properties or serialization."""

    def test_no_token_property(self):
        h = _make_handle()
        assert not hasattr(h, "token")
        assert not hasattr(h, "secret")
        assert not hasattr(h, "headers")

    def test_repr_hides_secrets(self):
        h = _make_handle()
        r = repr(h)
        assert "xoxb-secret-token" not in r
        assert "slack" in r  # provider is safe to show

    def test_str_hides_secrets(self):
        h = _make_handle()
        s = str(h)
        assert "xoxb-secret-token" not in s

    def test_not_serializable_getstate(self):
        h = _make_handle()
        with pytest.raises(TypeError, match="not serializable"):
            h.__getstate__()

    def test_not_picklable(self):
        h = _make_handle()
        with pytest.raises(TypeError):
            pickle.dumps(h)

    def test_secret_data_not_accessible(self):
        """No public attribute exposes the raw secret dict."""
        h = _make_handle()
        # _secret_data exists but is name-mangled by slots
        assert "xoxb-secret-token" not in str(dir(h))


class TestCredentialHandleDomainRestriction:
    """S3, S8: Handle validates target domain before authorizing."""

    def test_allowed_domain_returns_headers(self):
        h = _make_handle()
        headers = h.authorized_request_headers("https://api.slack.com/api/auth.test")
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer xoxb-secret-token"

    def test_disallowed_domain_raises(self):
        h = _make_handle()
        with pytest.raises(PermissionError, match="cannot access"):
            h.authorized_request_headers("https://evil.example.com/steal")

    def test_empty_allowed_domains_allows_all(self):
        h = _make_handle(allowed_domains=[])
        headers = h.authorized_request_headers("https://any.example.com/api")
        assert "Authorization" in headers

    def test_domain_check_case_insensitive(self):
        h = _make_handle(allowed_domains=["API.SLACK.COM"])
        headers = h.authorized_request_headers("https://api.slack.com/test")
        assert "Authorization" in headers

    def test_is_domain_allowed_true(self):
        h = _make_handle()
        assert h.is_domain_allowed("https://slack.com/api") is True

    def test_is_domain_allowed_false(self):
        h = _make_handle()
        assert h.is_domain_allowed("https://evil.com/api") is False

    def test_malformed_url_rejected(self):
        h = _make_handle()
        assert h.is_domain_allowed("not-a-url") is False


class TestCredentialHandleAuthTypes:
    """Different auth_type produce different headers."""

    def test_api_key_default_bearer(self):
        h = _make_handle(
            auth_type="api_key",
            secret_data={"api_key": "sk-test-key"},
            allowed_domains=[],
        )
        headers = h.authorized_request_headers("https://api.example.com")
        assert headers == {"Authorization": "Bearer sk-test-key"}

    def test_api_key_custom_header(self):
        h = _make_handle(
            auth_type="api_key",
            secret_data={
                "api_key": "my-key",
                "header_name": "X-API-Key",
                "header_prefix": "",
            },
            allowed_domains=[],
        )
        headers = h.authorized_request_headers("https://api.example.com")
        assert headers == {"X-API-Key": "my-key"}

    def test_oauth2_bearer_token(self):
        h = _make_handle(
            auth_type="oauth2",
            secret_data={"access_token": "oauth-tok-123"},
            allowed_domains=[],
        )
        headers = h.authorized_request_headers("https://api.example.com")
        assert headers == {"Authorization": "Bearer oauth-tok-123"}


class TestCredentialHandleMetadata:
    """Public metadata properties are safe to access."""

    def test_provider(self):
        h = _make_handle()
        assert h.provider == "slack"

    def test_auth_type(self):
        h = _make_handle()
        assert h.auth_type == "oauth2"

    def test_scopes_returns_copy(self):
        h = _make_handle()
        scopes = h.scopes
        scopes.append("INJECTED")
        assert "INJECTED" not in h.scopes

    def test_allowed_domains_returns_copy(self):
        h = _make_handle()
        domains = h.allowed_domains
        domains.append("evil.com")
        assert "evil.com" not in h.allowed_domains

    def test_source_default_empty(self):
        h = _make_handle()
        assert h.source == ""

    def test_source_set_on_construction(self):
        h = _make_handle(source="local_file")
        assert h.source == "local_file"

    def test_source_in_repr(self):
        h = _make_handle(source="env")
        assert "source='env'" in repr(h)

    def test_source_not_in_repr_when_empty(self):
        h = _make_handle()
        assert "source=" not in repr(h)


class TestAuthorizedRequest:
    """authorized_request() — preferred interface, token stays inside."""

    @respx.mock
    def test_successful_get(self):
        respx.get("https://api.slack.com/api/auth.test").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        h = _make_handle()
        resp = h.authorized_request("GET", "https://api.slack.com/api/auth.test")

        assert isinstance(resp, AuthorizedResponse)
        assert resp.status_code == 200
        assert '"ok": true' in resp.body.lower() or '"ok":true' in resp.body.lower()

    @respx.mock
    def test_post_with_json(self):
        route = respx.post("https://api.slack.com/api/chat.postMessage")
        route.mock(return_value=httpx.Response(200, json={"ok": True}))

        h = _make_handle()
        resp = h.authorized_request(
            "POST",
            "https://api.slack.com/api/chat.postMessage",
            json={"channel": "C123", "text": "hello"},
        )

        assert resp.status_code == 200
        # Verify auth header was sent
        req = route.calls[0].request
        assert "Bearer xoxb-secret-token" in req.headers.get("authorization", "")

    @respx.mock
    def test_domain_rejected(self):
        h = _make_handle()  # allowed_domains = ["slack.com", "api.slack.com"]

        with pytest.raises(PermissionError, match="cannot access"):
            h.authorized_request("GET", "https://evil.com/steal")

    @respx.mock
    def test_custom_headers_merged(self):
        route = respx.get("https://api.slack.com/test")
        route.mock(return_value=httpx.Response(200, text="ok"))

        h = _make_handle()
        resp = h.authorized_request(
            "GET",
            "https://api.slack.com/test",
            headers={"X-Custom": "value"},
        )

        req = route.calls[0].request
        assert req.headers.get("x-custom") == "value"
        assert "Bearer" in req.headers.get("authorization", "")

    @respx.mock
    def test_response_contains_no_secrets(self):
        respx.get("https://api.slack.com/test").mock(
            return_value=httpx.Response(200, text="response body")
        )
        h = _make_handle()
        resp = h.authorized_request("GET", "https://api.slack.com/test")

        # AuthorizedResponse should not contain the token
        assert "xoxb-secret-token" not in str(resp)
        assert "xoxb-secret-token" not in resp.body
