"""Edge-case and error-path tests for the AgentNode SDK."""
import json

import httpx
import pytest
import respx

from agentnode_sdk import AgentNode, AgentNodeClient
from agentnode_sdk.exceptions import (
    AgentNodeError,
    AuthError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)

BASE = "https://api.agentnode.net"


# ---------------------------------------------------------------------------
# Empty / missing data
# ---------------------------------------------------------------------------


@respx.mock
def test_search_empty_results():
    """Search returning zero hits should work without errors."""
    respx.post(f"{BASE}/v1/search").mock(return_value=httpx.Response(200, json={
        "query": "nonexistent-tool",
        "hits": [],
        "total": 0,
    }))

    with AgentNode(api_key="key") as client:
        result = client.search("nonexistent-tool")
        assert result["hits"] == []
        assert result["total"] == 0


@respx.mock
def test_search_empty_results_typed_client():
    """AgentNodeClient search with zero hits returns SearchResult with empty list."""
    respx.post(f"{BASE}/v1/search").mock(return_value=httpx.Response(200, json={
        "query": "nothing",
        "hits": [],
        "total": 0,
        "limit": 20,
        "offset": 0,
    }))

    with AgentNodeClient() as client:
        result = client.search("nothing")
        assert result.total == 0
        assert result.hits == []


@respx.mock
def test_package_null_optional_fields():
    """Package with None/null optional fields should parse without errors."""
    respx.get(f"{BASE}/v1/packages/minimal").mock(return_value=httpx.Response(200, json={
        "slug": "minimal",
        "name": "Minimal",
        "package_type": "toolpack",
        "summary": "Bare-minimum package",
        "description": None,
        "download_count": 0,
        "is_deprecated": False,
        "latest_version": None,
        "publisher": {"slug": "t", "display_name": "T", "trust_level": "unverified"},
        "blocks": {},
    }))

    with AgentNodeClient() as client:
        pkg = client.get_package("minimal")
        assert pkg.description is None
        assert pkg.latest_version is None
        assert pkg.download_count == 0


@respx.mock
def test_install_metadata_no_artifact_no_permissions():
    """Install metadata without artifact or permissions should parse cleanly."""
    respx.get(f"{BASE}/v1/packages/simple/install-info").mock(
        return_value=httpx.Response(200, json={
            "slug": "simple",
            "version": "0.1.0",
            "package_type": "toolpack",
            "install_mode": "reference",
            "hosting_type": "external",
            "runtime": "python",
            "entrypoint": None,
            "artifact": None,
            "capabilities": [],
            "dependencies": [],
            "permissions": None,
        })
    )

    with AgentNodeClient() as client:
        meta = client.get_install_metadata("simple")
        assert meta.artifact is None
        assert meta.permissions is None
        assert meta.entrypoint is None
        assert meta.capabilities == []
        assert meta.dependencies == []


@respx.mock
def test_search_hit_missing_optional_fields():
    """Search hit without optional fields should use defaults."""
    respx.post(f"{BASE}/v1/search").mock(return_value=httpx.Response(200, json={
        "query": "test",
        "hits": [{
            "slug": "test-tool",
            "name": "Test Tool",
            "package_type": "toolpack",
            "summary": "A test",
            # publisher_slug, trust_level, latest_version, runtime,
            # capability_ids, download_count all missing
        }],
        "total": 1,
        "limit": 20,
        "offset": 0,
    }))

    with AgentNodeClient() as client:
        result = client.search("test")
        hit = result.hits[0]
        assert hit.publisher_slug == ""
        assert hit.trust_level == "unverified"
        assert hit.latest_version is None
        assert hit.runtime is None
        assert hit.capability_ids == []
        assert hit.download_count == 0


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


@respx.mock
def test_auth_error_401():
    """401 should raise AuthError."""
    respx.get(f"{BASE}/v1/packages/test").mock(return_value=httpx.Response(401, json={
        "error": {"code": "INVALID_API_KEY", "message": "Invalid API key"},
    }))

    with AgentNode(api_key="bad-key") as client:
        with pytest.raises(AuthError) as exc_info:
            client.get_package("test")
        assert exc_info.value.code == "INVALID_API_KEY"
        assert "Invalid API key" in exc_info.value.message


@respx.mock
def test_auth_error_403():
    """403 should also raise AuthError."""
    respx.post(f"{BASE}/v1/search").mock(return_value=httpx.Response(403, json={
        "error": {"code": "FORBIDDEN", "message": "Insufficient permissions"},
    }))

    with AgentNode(api_key="limited-key") as client:
        with pytest.raises(AuthError) as exc_info:
            client.search("test")
        assert exc_info.value.code == "FORBIDDEN"


@respx.mock
def test_not_found_error_404():
    """404 should raise NotFoundError."""
    respx.get(f"{BASE}/v1/packages/ghost").mock(return_value=httpx.Response(404, json={
        "error": {"code": "PACKAGE_NOT_FOUND", "message": "Package 'ghost' not found"},
    }))

    with AgentNode(api_key="key") as client:
        with pytest.raises(NotFoundError) as exc_info:
            client.get_package("ghost")
        assert exc_info.value.code == "PACKAGE_NOT_FOUND"


@respx.mock
def test_validation_error_422():
    """422 should raise ValidationError."""
    respx.post(f"{BASE}/v1/packages/validate").mock(return_value=httpx.Response(422, json={
        "error": {"code": "INVALID_MANIFEST", "message": "Missing required field: name"},
    }))

    with AgentNode(api_key="key") as client:
        with pytest.raises(ValidationError) as exc_info:
            client.validate({})
        assert exc_info.value.code == "INVALID_MANIFEST"
        assert "name" in exc_info.value.message


@respx.mock
def test_rate_limit_error_429():
    """429 should raise RateLimitError."""
    respx.post(f"{BASE}/v1/search").mock(return_value=httpx.Response(429, json={
        "error": {"code": "RATE_LIMITED", "message": "Too many requests"},
    }))

    with AgentNode(api_key="key") as client:
        with pytest.raises(RateLimitError) as exc_info:
            client.search("test")
        assert exc_info.value.code == "RATE_LIMITED"
        assert "Too many requests" in exc_info.value.message


@respx.mock
def test_unknown_server_error():
    """500 with no matching class should raise base AgentNodeError."""
    respx.get(f"{BASE}/v1/packages/test").mock(return_value=httpx.Response(500, json={
        "error": {"code": "INTERNAL_ERROR", "message": "Something went wrong"},
    }))

    with AgentNode(api_key="key") as client:
        with pytest.raises(AgentNodeError) as exc_info:
            client.get_package("test")
        assert exc_info.value.code == "INTERNAL_ERROR"
        # Make sure it is the base class, not a subclass
        assert type(exc_info.value) is AgentNodeError


@respx.mock
def test_error_non_json_body():
    """Error response with non-JSON body should still raise with UNKNOWN code."""
    respx.get(f"{BASE}/v1/packages/test").mock(
        return_value=httpx.Response(502, text="Bad Gateway")
    )

    with AgentNode(api_key="key") as client:
        with pytest.raises(AgentNodeError) as exc_info:
            client.get_package("test")
        assert exc_info.value.code == "UNKNOWN"


@respx.mock
def test_error_missing_error_fields():
    """Error JSON without standard error.code/message should use defaults."""
    respx.get(f"{BASE}/v1/packages/test").mock(return_value=httpx.Response(400, json={
        "error": {},
    }))

    with AgentNode(api_key="key") as client:
        with pytest.raises(AgentNodeError) as exc_info:
            client.get_package("test")
        assert exc_info.value.code == "UNKNOWN"


# ---------------------------------------------------------------------------
# Request body construction (filter None values)
# ---------------------------------------------------------------------------


@respx.mock
def test_post_filters_empty_strings():
    """The _post helper should filter out empty-string values."""
    route = respx.post(f"{BASE}/v1/recommend").mock(
        return_value=httpx.Response(200, json={"recommendations": []})
    )

    with AgentNode(api_key="key") as client:
        # framework and runtime are empty strings, should be filtered
        client.recommend(["pdf_extraction"])

    body = json.loads(route.calls[0].request.content)
    assert "framework" not in body
    assert "runtime" not in body
    assert body["missing_capabilities"] == ["pdf_extraction"]


@respx.mock
def test_post_keeps_empty_lists_and_dicts():
    """The _post helper should keep empty lists and dicts (not filter them)."""
    route = respx.post(f"{BASE}/v1/resolve-upgrade").mock(
        return_value=httpx.Response(200, json={"recommendation": None})
    )

    with AgentNode(api_key="key") as client:
        client.resolve_upgrade(
            missing_capability="web_search",
            current_capabilities=[],
            policy={},
        )

    body = json.loads(route.calls[0].request.content)
    # Empty list and dict are kept as they are falsy but explicitly passed
    assert body["current_capabilities"] == []
    assert body["policy"] == {}


@respx.mock
def test_search_filters_falsy_params():
    """Search body should only include truthy parameters."""
    route = respx.post(f"{BASE}/v1/search").mock(
        return_value=httpx.Response(200, json={"query": "", "hits": [], "total": 0})
    )

    with AgentNode(api_key="key") as client:
        # All optional params are defaults (empty strings), only page is truthy
        client.search()

    body = json.loads(route.calls[0].request.content)
    # "q" is empty string -> filtered; "sort_by" is "relevance" -> kept; page=1 -> kept
    assert "q" not in body
    assert body["sort_by"] == "relevance"
    assert body["page"] == 1
    assert "capability_id" not in body
    assert "framework" not in body


# ---------------------------------------------------------------------------
# Auth header variants
# ---------------------------------------------------------------------------


@respx.mock
def test_api_key_auth_header():
    """AgentNode uses X-API-Key header."""
    route = respx.get(f"{BASE}/v1/packages/test").mock(
        return_value=httpx.Response(200, json={"slug": "test"})
    )

    with AgentNode(api_key="ank_mykey") as client:
        client.get_package("test")

    assert route.calls[0].request.headers["x-api-key"] == "ank_mykey"


@respx.mock
def test_agentnode_client_api_key_header():
    """AgentNodeClient with api_key uses X-API-Key header."""
    route = respx.get(f"{BASE}/v1/packages/test").mock(return_value=httpx.Response(200, json={
        "slug": "test", "name": "Test", "package_type": "toolpack",
        "summary": "T", "description": None, "download_count": 0,
        "is_deprecated": False, "latest_version": None,
        "publisher": {"slug": "t", "display_name": "T", "trust_level": "unverified"},
        "blocks": {},
    }))

    with AgentNodeClient(api_key="ank_key123") as client:
        client.get_package("test")

    assert route.calls[0].request.headers["x-api-key"] == "ank_key123"


@respx.mock
def test_agentnode_client_token_auth_header():
    """AgentNodeClient with token uses Bearer Authorization header."""
    route = respx.get(f"{BASE}/v1/packages/test").mock(return_value=httpx.Response(200, json={
        "slug": "test", "name": "Test", "package_type": "toolpack",
        "summary": "T", "description": None, "download_count": 0,
        "is_deprecated": False, "latest_version": None,
        "publisher": {"slug": "t", "display_name": "T", "trust_level": "unverified"},
        "blocks": {},
    }))

    with AgentNodeClient(token="jwt_token_value") as client:
        client.get_package("test")

    assert route.calls[0].request.headers["authorization"] == "Bearer jwt_token_value"


@respx.mock
def test_agentnode_client_api_key_takes_precedence_over_token():
    """When both api_key and token are given, api_key takes precedence."""
    route = respx.get(f"{BASE}/v1/packages/test").mock(return_value=httpx.Response(200, json={
        "slug": "test", "name": "Test", "package_type": "toolpack",
        "summary": "T", "description": None, "download_count": 0,
        "is_deprecated": False, "latest_version": None,
        "publisher": {"slug": "t", "display_name": "T", "trust_level": "unverified"},
        "blocks": {},
    }))

    with AgentNodeClient(api_key="ank_key", token="jwt_token") as client:
        client.get_package("test")

    headers = route.calls[0].request.headers
    assert headers["x-api-key"] == "ank_key"
    assert "authorization" not in headers


@respx.mock
def test_agentnode_client_no_auth():
    """AgentNodeClient with no auth should not send auth headers."""
    route = respx.get(f"{BASE}/v1/packages/test").mock(return_value=httpx.Response(200, json={
        "slug": "test", "name": "Test", "package_type": "toolpack",
        "summary": "T", "description": None, "download_count": 0,
        "is_deprecated": False, "latest_version": None,
        "publisher": {"slug": "t", "display_name": "T", "trust_level": "unverified"},
        "blocks": {},
    }))

    with AgentNodeClient() as client:
        client.get_package("test")

    headers = route.calls[0].request.headers
    assert "x-api-key" not in headers
    assert "authorization" not in headers


# ---------------------------------------------------------------------------
# Context manager / lifecycle
# ---------------------------------------------------------------------------


def test_sync_context_manager():
    """AgentNode context manager should return self and close the client."""
    client = AgentNode(api_key="key")
    with client as ctx:
        assert ctx is client
    assert client._client.is_closed


def test_typed_client_context_manager():
    """AgentNodeClient context manager should return self and close the client."""
    client = AgentNodeClient(api_key="key")
    with client as ctx:
        assert ctx is client
    assert client._client.is_closed


# ---------------------------------------------------------------------------
# Error class hierarchy
# ---------------------------------------------------------------------------


def test_error_hierarchy():
    """All specific errors should be subclasses of AgentNodeError."""
    assert issubclass(AuthError, AgentNodeError)
    assert issubclass(NotFoundError, AgentNodeError)
    assert issubclass(ValidationError, AgentNodeError)
    assert issubclass(RateLimitError, AgentNodeError)


def test_error_attributes():
    """AgentNodeError should expose code and message attributes."""
    err = AgentNodeError("TEST_CODE", "test message")
    assert err.code == "TEST_CODE"
    assert err.message == "test message"
    assert "[TEST_CODE] test message" in str(err)
