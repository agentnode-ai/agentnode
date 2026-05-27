"""Unit tests for AgentNode SDK client."""
import base64
import json
from types import MappingProxyType
from unittest.mock import patch

import httpx
import pytest
import respx

from agentnode_sdk import AgentNodeClient
from agentnode_sdk.registry_trust import RegistryKey

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
def test_get_install_metadata_mcp_server():
    """install-info with mcp_server block is parsed into InstallMetadata.mcp_server."""
    respx.get(f"{BASE}/v1/packages/mcp-filesystem/install-info").mock(
        return_value=httpx.Response(200, json={
            "slug": "mcp-filesystem",
            "version": "0.1.0",
            "package_type": "toolpack",
            "install_mode": "package",
            "hosting_type": "agentnode_hosted",
            "runtime": "mcp",
            "entrypoint": None,
            "artifact": None,
            "capabilities": [],
            "dependencies": [],
            "permissions": None,
            "mcp_server": {
                "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem@2025.3.28"],
                "transport": "stdio",
                "npm_package": "@modelcontextprotocol/server-filesystem",
                "source_repo": "https://github.com/modelcontextprotocol/servers",
                "env_keys": [],
            },
        })
    )

    with AgentNodeClient() as client:
        meta = client.get_install_metadata("mcp-filesystem")
        assert meta.slug == "mcp-filesystem"
        assert meta.runtime == "mcp"
        assert meta.mcp_server is not None
        assert meta.mcp_server["command"] == [
            "npx", "-y", "@modelcontextprotocol/server-filesystem@2025.3.28",
        ]
        assert meta.mcp_server["transport"] == "stdio"
        assert meta.mcp_server["env_keys"] == []


@respx.mock
def test_get_install_metadata_no_mcp_server():
    """Non-MCP install-info leaves mcp_server as None."""
    respx.get(f"{BASE}/v1/packages/pdf-reader/install-info").mock(
        return_value=httpx.Response(200, json={
            "slug": "pdf-reader",
            "version": "1.0.0",
            "package_type": "toolpack",
            "install_mode": "package",
            "hosting_type": "agentnode_hosted",
            "runtime": "python",
            "entrypoint": "pdf_reader.tool",
            "artifact": None,
            "capabilities": [],
            "dependencies": [],
            "permissions": None,
        })
    )

    with AgentNodeClient() as client:
        meta = client.get_install_metadata("pdf-reader")
        assert meta.mcp_server is None


def _mock_install_endpoints(slug, install_info_json):
    """Mock all endpoints that client.install() hits: install-info, package detail, POST install."""
    respx.get(f"{BASE}/v1/packages/{slug}/install-info").mock(
        return_value=httpx.Response(200, json=install_info_json)
    )
    respx.get(f"{BASE}/v1/packages/{slug}").mock(
        return_value=httpx.Response(200, json={
            "slug": slug, "name": slug, "package_type": "toolpack",
            "summary": "Test", "description": None, "download_count": 0,
            "is_deprecated": False, "latest_version": None,
            "publisher": {"slug": "test-pub", "display_name": "Test", "trust_level": "verified"},
            "blocks": {},
        })
    )
    respx.post(f"{BASE}/v1/packages/{slug}/install").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )


@respx.mock
def test_install_mcp_forwards_runtime_and_command(tmp_path, monkeypatch):
    """client.install() forwards runtime, mcp_command and mcp_env_keys to install_package()."""
    mcp_server_block = {
        "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem@2025.3.28"],
        "transport": "stdio",
        "npm_package": "@modelcontextprotocol/server-filesystem",
        "env_keys": ["BRAVE_API_KEY"],
    }

    _mock_install_endpoints("mcp-filesystem", {
        "slug": "mcp-filesystem",
        "version": "0.1.0",
        "package_type": "toolpack",
        "install_mode": "package",
        "hosting_type": "agentnode_hosted",
        "runtime": "mcp",
        "entrypoint": None,
        "artifact": None,
        "capabilities": [],
        "dependencies": [],
        "permissions": None,
        "mcp_server": mcp_server_block,
    })

    captured_kwargs = {}

    def fake_install_package(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "slug": "mcp-filesystem",
            "version": "0.1.0",
            "installed": True,
            "message": "ok",
        }

    monkeypatch.setattr("agentnode_sdk.client.install_package", fake_install_package)

    with AgentNodeClient() as client:
        result = client.install("mcp-filesystem")

    assert result.installed is True
    assert captured_kwargs["runtime"] == "mcp"
    assert captured_kwargs["mcp_command"] == [
        "npx", "-y", "@modelcontextprotocol/server-filesystem@2025.3.28",
    ]
    assert captured_kwargs["mcp_env_keys"] == ["BRAVE_API_KEY"]


@respx.mock
def test_install_non_mcp_no_mcp_command(tmp_path, monkeypatch):
    """client.install() passes mcp_command=None for non-MCP packages."""
    _mock_install_endpoints("pdf-reader", {
        "slug": "pdf-reader",
        "version": "1.0.0",
        "package_type": "toolpack",
        "install_mode": "package",
        "hosting_type": "agentnode_hosted",
        "runtime": "python",
        "entrypoint": "pdf_reader.tool",
        "artifact": {"url": "https://s3.example.com/a.tar.gz", "hash_sha256": "abc123", "size_bytes": 1000},
        "capabilities": [],
        "dependencies": [],
        "permissions": None,
    })

    captured_kwargs = {}

    def fake_install_package(**kwargs):
        captured_kwargs.update(kwargs)
        return {
            "slug": "pdf-reader",
            "version": "1.0.0",
            "installed": True,
            "message": "ok",
        }

    monkeypatch.setattr("agentnode_sdk.client.install_package", fake_install_package)

    with AgentNodeClient() as client:
        result = client.install("pdf-reader")

    assert result.installed is True
    assert captured_kwargs["runtime"] == "python"
    assert captured_kwargs["mcp_command"] is None
    assert captured_kwargs["mcp_env_keys"] is None


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


# ---------------------------------------------------------------------------
# Sprint B tests: P1-SDK3, P1-SDK4
# ---------------------------------------------------------------------------

@respx.mock
def test_p1_sdk3_non_dict_error_body():
    """P1-SDK3: _request must not crash if server returns a non-dict JSON
    error body (e.g. a list or a bare string)."""
    respx.get(f"{BASE}/v1/packages/broken").mock(
        return_value=httpx.Response(500, json=["upstream", "failure"])
    )
    from agentnode_sdk.exceptions import AgentNodeError
    with AgentNodeClient(api_key="k") as client:
        with pytest.raises(AgentNodeError) as exc:
            client.get_package("broken")
        assert exc.value.code == "UNKNOWN"


@respx.mock
def test_p1_sdk4_non_json_response():
    """P1-SDK4: a 2xx response with content-type text/html must raise
    AgentNodeError instead of crashing in resp.json()."""
    respx.get(f"{BASE}/v1/packages/html").mock(
        return_value=httpx.Response(
            200,
            content=b"<html>maintenance</html>",
            headers={"content-type": "text/html"},
        )
    )
    from agentnode_sdk.exceptions import AgentNodeError
    with AgentNodeClient(api_key="k") as client:
        with pytest.raises(AgentNodeError):
            client.get_package("html")


# ---------------------------------------------------------------------------
# TG-4: Registry response authenticity integration
# ---------------------------------------------------------------------------

def _patch_registry_keys(keys):
    return patch(
        "agentnode_sdk.registry_trust.REGISTRY_KEYS",
        MappingProxyType(keys),
    )


_FAKE_KEY = RegistryKey(
    key_id="registry-2026",
    algorithm="ed25519",
    public_key=base64.b64encode(b"\x00" * 32).decode(),
    not_after="2099-12-31",
)

_PACKAGE_JSON = {
    "slug": "test-pack", "name": "Test", "package_type": "toolpack",
    "summary": "Test", "description": None, "download_count": 0,
    "is_deprecated": False, "latest_version": None,
    "publisher": {"slug": "t", "display_name": "T", "trust_level": "verified"},
    "blocks": {},
}


@respx.mock
def test_tg4_missing_signature_blocks_trust_critical():
    """Enforcement active + no signature header on GET /packages/{slug} → deny."""
    respx.get(f"{BASE}/v1/packages/test-pack").mock(
        return_value=httpx.Response(200, json=_PACKAGE_JSON)
    )
    from agentnode_sdk.exceptions import AgentNodeError
    with _patch_registry_keys({"registry-2026": _FAKE_KEY}):
        with AgentNodeClient(api_key="k") as client:
            with pytest.raises(AgentNodeError) as exc:
                client.get_package("test-pack")
            assert exc.value.code == "REGISTRY_SIGNATURE_MISSING"


@respx.mock
def test_tg4_bootstrap_allows_trust_critical():
    """REGISTRY_KEYS empty → BOOTSTRAP → request succeeds."""
    respx.get(f"{BASE}/v1/packages/test-pack").mock(
        return_value=httpx.Response(200, json=_PACKAGE_JSON)
    )
    with AgentNodeClient(api_key="k") as client:
        pkg = client.get_package("test-pack")
    assert pkg.slug == "test-pack"


@respx.mock
def test_tg4_non_trust_critical_skips_check():
    """Non-trust-critical endpoints skip signature check even with keys."""
    respx.post(f"{BASE}/v1/search").mock(
        return_value=httpx.Response(200, json={
            "query": "test", "hits": [], "total": 0, "page": 1, "per_page": 20,
        })
    )
    with _patch_registry_keys({"registry-2026": _FAKE_KEY}):
        with AgentNodeClient(api_key="k") as client:
            results = client.search("test")
    assert results.total == 0


@respx.mock
def test_tg4_invalid_signature_blocks():
    """Enforcement active + bad signature → REGISTRY_SIGNATURE_INVALID."""
    bad_sig = base64.b64encode(b"\xff" * 64).decode()
    respx.get(f"{BASE}/v1/packages/test-pack").mock(
        return_value=httpx.Response(
            200,
            json=_PACKAGE_JSON,
            headers={"X-AgentNode-Signature": f"ed25519:registry-2026:{bad_sig}"},
        )
    )
    from agentnode_sdk.exceptions import AgentNodeError
    with _patch_registry_keys({"registry-2026": _FAKE_KEY}):
        with AgentNodeClient(api_key="k") as client:
            with pytest.raises(AgentNodeError) as exc:
                client.get_package("test-pack")
            assert exc.value.code == "REGISTRY_SIGNATURE_INVALID"


@respx.mock
def test_tg4_unknown_key_blocks():
    """Signature with unknown key_id → REGISTRY_KEY_UNKNOWN."""
    sig = base64.b64encode(b"\x00" * 64).decode()
    respx.get(f"{BASE}/v1/packages/test-pack").mock(
        return_value=httpx.Response(
            200,
            json=_PACKAGE_JSON,
            headers={"X-AgentNode-Signature": f"ed25519:unknown-key:{sig}"},
        )
    )
    from agentnode_sdk.exceptions import AgentNodeError
    with _patch_registry_keys({"registry-2026": _FAKE_KEY}):
        with AgentNodeClient(api_key="k") as client:
            with pytest.raises(AgentNodeError) as exc:
                client.get_package("test-pack")
            assert exc.value.code == "REGISTRY_KEY_UNKNOWN"
