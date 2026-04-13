"""Unit tests for AsyncAgentNode client."""
import httpx
import pytest
import respx

from agentnode_sdk import AsyncAgentNode
from agentnode_sdk.exceptions import AgentNodeError, NotFoundError

BASE = "https://api.agentnode.net"


@pytest.mark.asyncio
@respx.mock
async def test_search():
    respx.post(f"{BASE}/v1/search").mock(return_value=httpx.Response(200, json={
        "query": "pdf",
        "hits": [{
            "slug": "pdf-reader",
            "name": "PDF Reader",
            "package_type": "toolpack",
            "summary": "Read PDFs",
        }],
        "total": 1,
    }))

    async with AsyncAgentNode(api_key="test-key") as client:
        result = await client.search("pdf")
        assert result["total"] == 1
        assert result["hits"][0]["slug"] == "pdf-reader"


@pytest.mark.asyncio
@respx.mock
async def test_search_with_filters():
    route = respx.post(f"{BASE}/v1/search").mock(return_value=httpx.Response(200, json={
        "query": "pdf",
        "hits": [],
        "total": 0,
    }))

    async with AsyncAgentNode(api_key="test-key") as client:
        await client.search("pdf", capability_id="pdf_extraction", framework="langchain")

    request = route.calls[0].request
    import json
    body = json.loads(request.content)
    assert body["q"] == "pdf"
    assert body["capability_id"] == "pdf_extraction"
    assert body["framework"] == "langchain"


@pytest.mark.asyncio
@respx.mock
async def test_resolve_upgrade():
    respx.post(f"{BASE}/v1/resolve-upgrade").mock(return_value=httpx.Response(200, json={
        "recommendation": {
            "slug": "pdf-reader",
            "name": "PDF Reader",
            "version": "1.0.0",
        },
        "alternatives": [],
    }))

    async with AsyncAgentNode(api_key="test-key") as client:
        result = await client.resolve_upgrade(
            missing_capability="pdf_extraction",
            framework="langchain",
            current_capabilities=["web_search"],
        )
        assert result["recommendation"]["slug"] == "pdf-reader"


@pytest.mark.asyncio
@respx.mock
async def test_check_policy():
    respx.post(f"{BASE}/v1/check-policy").mock(return_value=httpx.Response(200, json={
        "allowed": True,
        "violations": [],
    }))

    async with AsyncAgentNode(api_key="test-key") as client:
        result = await client.check_policy("pdf-reader", framework="langchain")
        assert result["allowed"] is True
        assert result["violations"] == []


@pytest.mark.asyncio
@respx.mock
async def test_get_install_metadata():
    respx.get(f"{BASE}/v1/packages/pdf-reader/install-info").mock(
        return_value=httpx.Response(200, json={
            "slug": "pdf-reader",
            "version": "1.0.0",
            "package_type": "toolpack",
            "install_mode": "package",
            "hosting_type": "agentnode_hosted",
            "runtime": "python",
            "entrypoint": "pdf_reader.tool",
            "artifact": {
                "url": "https://s3.example.com/artifact.tar.gz",
                "hash_sha256": "abc123",
                "size_bytes": 1000,
            },
            "capabilities": [
                {"name": "extract", "capability_id": "pdf_extraction", "capability_type": "tool"},
            ],
            "dependencies": [],
            "permissions": {
                "network_level": "none",
                "filesystem_level": "temp",
                "code_execution_level": "none",
                "data_access_level": "input_only",
                "user_approval_level": "never",
            },
        })
    )

    async with AsyncAgentNode(api_key="test-key") as client:
        result = await client.get_install_metadata("pdf-reader")
        assert result["slug"] == "pdf-reader"
        assert result["entrypoint"] == "pdf_reader.tool"
        assert result["artifact"]["url"] == "https://s3.example.com/artifact.tar.gz"


@pytest.mark.asyncio
@respx.mock
async def test_get_install_metadata_with_version():
    route = respx.get(f"{BASE}/v1/packages/pdf-reader/install-info").mock(
        return_value=httpx.Response(200, json={
            "slug": "pdf-reader",
            "version": "2.0.0",
            "package_type": "toolpack",
            "install_mode": "package",
            "hosting_type": "agentnode_hosted",
            "runtime": "python",
        })
    )

    async with AsyncAgentNode(api_key="test-key") as client:
        result = await client.get_install_metadata("pdf-reader", version="2.0.0")
        assert result["version"] == "2.0.0"

    assert "version=2.0.0" in str(route.calls[0].request.url)


@pytest.mark.asyncio
@respx.mock
async def test_get_package():
    respx.get(f"{BASE}/v1/packages/pdf-reader").mock(return_value=httpx.Response(200, json={
        "slug": "pdf-reader",
        "name": "PDF Reader",
        "package_type": "toolpack",
        "summary": "Read PDFs",
        "description": "A great PDF reader",
        "download_count": 42,
        "is_deprecated": False,
        "latest_version": {"version_number": "1.0.0", "channel": "stable"},
    }))

    async with AsyncAgentNode(api_key="test-key") as client:
        result = await client.get_package("pdf-reader")
        assert result["slug"] == "pdf-reader"
        assert result["download_count"] == 42


@pytest.mark.asyncio
@respx.mock
async def test_validate():
    respx.post(f"{BASE}/v1/packages/validate").mock(return_value=httpx.Response(200, json={
        "valid": True,
        "errors": [],
        "warnings": [],
    }))

    async with AsyncAgentNode(api_key="test-key") as client:
        result = await client.validate({"name": "my-tool", "version": "1.0.0"})
        assert result["valid"] is True


@pytest.mark.asyncio
@respx.mock
async def test_install():
    respx.post(f"{BASE}/v1/packages/pdf-reader/install").mock(
        return_value=httpx.Response(200, json={
            "slug": "pdf-reader",
            "version": "1.0.0",
            "artifact_url": "https://s3.example.com/artifact.tar.gz",
            "install_id": "inst_abc123",
        })
    )

    async with AsyncAgentNode(api_key="test-key") as client:
        result = await client.install("pdf-reader", version="1.0.0")
        assert result["install_id"] == "inst_abc123"
        assert result["artifact_url"] == "https://s3.example.com/artifact.tar.gz"


@pytest.mark.asyncio
@respx.mock
async def test_install_default_body():
    route = respx.post(f"{BASE}/v1/packages/my-tool/install").mock(
        return_value=httpx.Response(200, json={
            "slug": "my-tool",
            "version": "1.0.0",
            "install_id": "inst_xyz",
        })
    )

    async with AsyncAgentNode(api_key="test-key") as client:
        await client.install("my-tool")

    import json
    body = json.loads(route.calls[0].request.content)
    assert body["source"] == "sdk"
    assert body["event_type"] == "install"
    assert "version" not in body


@pytest.mark.asyncio
@respx.mock
async def test_recommend():
    respx.post(f"{BASE}/v1/recommend").mock(return_value=httpx.Response(200, json={
        "recommendations": [
            {"slug": "pdf-reader", "name": "PDF Reader", "score": 0.9},
            {"slug": "doc-parser", "name": "Doc Parser", "score": 0.7},
        ],
    }))

    async with AsyncAgentNode(api_key="test-key") as client:
        result = await client.recommend(["pdf_extraction", "doc_parsing"], framework="langchain")
        assert len(result["recommendations"]) == 2
        assert result["recommendations"][0]["slug"] == "pdf-reader"


@pytest.mark.asyncio
@respx.mock
async def test_error_not_found():
    respx.get(f"{BASE}/v1/packages/nonexistent").mock(return_value=httpx.Response(404, json={
        "error": {"code": "PACKAGE_NOT_FOUND", "message": "Not found"},
    }))

    async with AsyncAgentNode(api_key="test-key") as client:
        with pytest.raises(NotFoundError) as exc_info:
            await client.get_package("nonexistent")
        assert exc_info.value.code == "PACKAGE_NOT_FOUND"


@pytest.mark.asyncio
@respx.mock
async def test_api_key_header():
    route = respx.get(f"{BASE}/v1/packages/test").mock(return_value=httpx.Response(200, json={
        "slug": "test",
        "name": "Test",
    }))

    async with AsyncAgentNode(api_key="ank_test123") as client:
        await client.get_package("test")

    assert route.called
    assert route.calls[0].request.headers["x-api-key"] == "ank_test123"


@pytest.mark.asyncio
@respx.mock
async def test_custom_base_url():
    route = respx.get("https://custom.api.com/v1/packages/test").mock(
        return_value=httpx.Response(200, json={"slug": "test"})
    )

    async with AsyncAgentNode(api_key="key", base_url="https://custom.api.com/v1/") as client:
        await client.get_package("test")

    assert route.called


@pytest.mark.asyncio
async def test_context_manager():
    """Verify __aenter__ returns self and __aexit__ closes the client."""
    client = AsyncAgentNode(api_key="test-key")
    async with client as ctx:
        assert ctx is client
    # After exiting, the internal client should be closed
    assert client._client.is_closed


# ---------------------------------------------------------------------------
# Sprint B tests: P0-04, P1-SDK3, P1-SDK4
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_p0_04_v1_prefix_added_when_missing():
    """P0-04: AsyncAgentNode must append /v1 when base_url lacks it.
    Previously it did not, producing 404s against production."""
    route = respx.get("http://localhost:8000/v1/packages/foo").mock(
        return_value=httpx.Response(200, json={"slug": "foo", "name": "Foo"})
    )
    async with AsyncAgentNode(api_key="k", base_url="http://localhost:8000") as client:
        await client.get_package("foo")
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_p0_04_v1_prefix_not_duplicated():
    """If the caller already specifies /v1, don't double it to /v1/v1."""
    route = respx.get("http://localhost:8000/v1/packages/foo").mock(
        return_value=httpx.Response(200, json={"slug": "foo", "name": "Foo"})
    )
    async with AsyncAgentNode(api_key="k", base_url="http://localhost:8000/v1") as client:
        await client.get_package("foo")
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_p1_sdk3_non_dict_error_body_does_not_crash():
    """P1-SDK3: an error response whose JSON body is a list/string must not
    crash the SDK's _handle path."""
    respx.get(f"{BASE}/v1/packages/broken").mock(
        return_value=httpx.Response(500, json=["upstream", "failure"])
    )
    async with AsyncAgentNode(api_key="k") as client:
        with pytest.raises(AgentNodeError) as exc_info:
            await client.get_package("broken")
        assert exc_info.value.code == "UNKNOWN"


@pytest.mark.asyncio
@respx.mock
async def test_p1_sdk4_non_json_response_raises_agentnode_error():
    """P1-SDK4: a 2xx with an HTML body must raise AgentNodeError instead
    of crashing in response.json()."""
    respx.get(f"{BASE}/v1/packages/html").mock(
        return_value=httpx.Response(
            200,
            content=b"<html><body>Maintenance</body></html>",
            headers={"content-type": "text/html"},
        )
    )
    async with AsyncAgentNode(api_key="k") as client:
        with pytest.raises(AgentNodeError):
            await client.get_package("html")
