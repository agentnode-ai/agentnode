"""Unit tests for AgentNode SDK client."""
import json

import httpx
import pytest
import respx

from agentnode_sdk import AgentNodeClient

BASE = "https://api.agentnode.net"


@respx.mock
def test_search():
    respx.post(f"{BASE}/v1/search").mock(return_value=httpx.Response(200, json={
        "query": "pdf",
        "hits": [{
            "slug": "pdf-reader",
            "name": "PDF Reader",
            "package_type": "toolpack",
            "summary": "Read PDFs",
            "publisher_slug": "test",
            "trust_level": "verified",
            "latest_version": "1.0.0",
            "runtime": "python",
            "capability_ids": ["pdf_extraction"],
            "download_count": 100,
        }],
        "total": 1,
        "limit": 20,
        "offset": 0,
    }))

    with AgentNodeClient() as client:
        result = client.search("pdf")
        assert result.total == 1
        assert result.hits[0].slug == "pdf-reader"
        assert result.hits[0].capability_ids == ["pdf_extraction"]


@respx.mock
def test_resolve():
    respx.post(f"{BASE}/v1/resolve").mock(return_value=httpx.Response(200, json={
        "results": [{
            "slug": "pdf-reader",
            "name": "PDF Reader",
            "package_type": "toolpack",
            "summary": "Read PDFs",
            "version": "1.0.0",
            "publisher_slug": "test",
            "trust_level": "verified",
            "score": 0.85,
            "breakdown": {
                "capability": 1.0,
                "framework": 1.0,
                "runtime": 1.0,
                "trust": 0.5,
                "permissions": 0.9,
            },
            "matched_capabilities": ["pdf_extraction"],
        }],
        "total": 1,
    }))

    with AgentNodeClient() as client:
        result = client.resolve(["pdf_extraction"])
        assert result.total == 1
        assert result.results[0].score == 0.85
        assert result.results[0].breakdown.capability == 1.0


@respx.mock
def test_get_package():
    respx.get(f"{BASE}/v1/packages/pdf-reader").mock(return_value=httpx.Response(200, json={
        "slug": "pdf-reader",
        "name": "PDF Reader",
        "package_type": "toolpack",
        "summary": "Read PDFs",
        "description": "A great PDF reader",
        "download_count": 42,
        "is_deprecated": False,
        "latest_version": {"version_number": "1.0.0", "channel": "stable", "published_at": "2026-01-01T00:00:00Z"},
        "publisher": {"slug": "test", "display_name": "Test", "trust_level": "verified"},
        "blocks": {},
    }))

    with AgentNodeClient() as client:
        pkg = client.get_package("pdf-reader")
        assert pkg.slug == "pdf-reader"
        assert pkg.latest_version == "1.0.0"
        assert pkg.download_count == 42


@respx.mock
def test_get_install_metadata():
    respx.get(f"{BASE}/v1/packages/pdf-reader/install-info").mock(return_value=httpx.Response(200, json={
        "slug": "pdf-reader",
        "version": "1.0.0",
        "package_type": "toolpack",
        "install_mode": "package",
        "hosting_type": "agentnode_hosted",
        "runtime": "python",
        "entrypoint": "pdf_reader.tool",
        "artifact": {"url": "https://s3.example.com/artifact.tar.gz", "hash_sha256": "abc123", "size_bytes": 1000},
        "capabilities": [{"name": "extract", "capability_id": "pdf_extraction", "capability_type": "tool"}],
        "dependencies": [],
        "permissions": {
            "network_level": "none",
            "filesystem_level": "temp",
            "code_execution_level": "none",
            "data_access_level": "input_only",
            "user_approval_level": "never",
        },
        "published_at": "2026-01-01T00:00:00Z",
    }))

    with AgentNodeClient() as client:
        meta = client.get_install_metadata("pdf-reader")
        assert meta.slug == "pdf-reader"
        assert meta.entrypoint == "pdf_reader.tool"
        assert meta.artifact.url == "https://s3.example.com/artifact.tar.gz"
        assert len(meta.capabilities) == 1
        assert meta.permissions.network_level == "none"


@respx.mock
def test_error_handling():
    respx.get(f"{BASE}/v1/packages/nonexistent").mock(return_value=httpx.Response(404, json={
        "error": {"code": "PACKAGE_NOT_FOUND", "message": "Not found", "details": {}}
    }))

    with AgentNodeClient() as client:
        with pytest.raises(Exception) as exc_info:
            client.get_package("nonexistent")
        assert "PACKAGE_NOT_FOUND" in str(exc_info.value)


@respx.mock
def test_api_key_auth():
    route = respx.get(f"{BASE}/v1/packages/test").mock(return_value=httpx.Response(200, json={
        "slug": "test", "name": "Test", "package_type": "toolpack",
        "summary": "Test", "description": None, "download_count": 0,
        "is_deprecated": False, "latest_version": None,
        "publisher": {"slug": "t", "display_name": "T", "trust_level": "unverified"},
        "blocks": {},
    }))

    with AgentNodeClient(api_key="ank_test123") as client:
        client.get_package("test")

    assert route.called
    assert route.calls[0].request.headers["x-api-key"] == "ank_test123"
