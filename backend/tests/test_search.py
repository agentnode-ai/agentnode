"""Integration tests for search endpoint (Meilisearch is mocked)."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


MOCK_MEILI_RESPONSE = {
    "hits": [
        {
            "slug": "pdf-reader-pack",
            "name": "PDF Reader Pack",
            "package_type": "toolpack",
            "summary": "Extract text from PDFs",
            "publisher_name": "Test Publisher",
            "publisher_slug": "test-pub",
            "trust_level": "verified",
            "latest_version": "1.0.0",
            "runtime": "python",
            "capability_ids": ["pdf_extraction"],
            "tags": ["pdf", "extraction"],
            "frameworks": ["generic"],
            "download_count": 42,
            "is_deprecated": False,
            "network_level": "none",
            "filesystem_level": "temp",
            "code_execution_level": "none",
            "has_connector": False,
        },
    ],
    "estimatedTotalHits": 1,
}


def _mock_meili_post(response_data, status_code=200):
    """Create an AsyncMock for the shared search client's .post() method."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = response_data
    return AsyncMock(return_value=mock_response)


@pytest.mark.asyncio
@patch("app.search.router._get_search_client")
async def test_search_basic(mock_get_client, client):
    mock_client = MagicMock()
    mock_client.post = _mock_meili_post(MOCK_MEILI_RESPONSE)
    mock_get_client.return_value = mock_client

    resp = await client.post("/v1/search", json={"q": "pdf"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "pdf"
    assert data["total"] == 1
    assert len(data["hits"]) == 1
    assert data["hits"][0]["slug"] == "pdf-reader-pack"
    assert data["hits"][0]["capability_ids"] == ["pdf_extraction"]
    assert data["hits"][0]["network_level"] == "none"
    assert data["hits"][0]["filesystem_level"] == "temp"
    assert data["hits"][0]["code_execution_level"] == "none"
    assert data["hits"][0]["has_connector"] is False


@pytest.mark.asyncio
@patch("app.search.router._get_search_client")
async def test_search_permission_fields_null_when_missing(mock_get_client, client):
    """Permission fields default to null when not in Meili document."""
    meili_response = {
        "hits": [{
            "slug": "old-pack",
            "name": "Old Pack",
            "package_type": "toolpack",
            "summary": "Pre-permission-fields package",
            "publisher_name": "Test",
            "publisher_slug": "test",
            "trust_level": "verified",
            "capability_ids": [],
            "tags": [],
            "frameworks": [],
            "download_count": 0,
            "is_deprecated": False,
        }],
        "estimatedTotalHits": 1,
    }
    mock_client = MagicMock()
    mock_client.post = _mock_meili_post(meili_response)
    mock_get_client.return_value = mock_client

    resp = await client.post("/v1/search", json={"q": "old"})
    assert resp.status_code == 200
    hit = resp.json()["hits"][0]
    assert hit["network_level"] is None
    assert hit["filesystem_level"] is None
    assert hit["code_execution_level"] is None
    assert hit["has_connector"] is None


@pytest.mark.asyncio
@patch("app.search.router._get_search_client")
async def test_search_with_filters(mock_get_client, client):
    mock_client = MagicMock()
    mock_client.post = _mock_meili_post({"hits": [], "estimatedTotalHits": 0})
    mock_get_client.return_value = mock_client

    resp = await client.post("/v1/search", json={
        "q": "pdf",
        "package_type": "toolpack",
        "capability_id": "pdf_extraction",
        "framework": "langchain",
    })
    assert resp.status_code == 200

    call_args = mock_client.post.call_args
    sent_body = call_args.kwargs.get("json") or call_args[1].get("json")
    filters = sent_body["filter"]
    assert 'package_type = "toolpack"' in filters
    assert 'capability_ids = "pdf_extraction"' in filters
    assert 'frameworks = "langchain"' in filters


@pytest.mark.asyncio
@patch("app.search.router._get_search_client")
async def test_search_empty_query(mock_get_client, client):
    mock_client = MagicMock()
    mock_client.post = _mock_meili_post({"hits": [], "estimatedTotalHits": 0})
    mock_get_client.return_value = mock_client

    resp = await client.post("/v1/search", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == ""


@pytest.mark.asyncio
@patch("app.search.router._get_search_client")
async def test_search_meili_failure_returns_empty(mock_get_client, client):
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
    mock_get_client.return_value = mock_client

    resp = await client.post("/v1/search", json={"q": "pdf"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["hits"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
@patch("app.search.router._get_search_client")
async def test_search_with_sort(mock_get_client, client):
    mock_client = MagicMock()
    mock_client.post = _mock_meili_post({"hits": [], "estimatedTotalHits": 0})
    mock_get_client.return_value = mock_client

    resp = await client.post("/v1/search", json={"q": "test", "sort_by": "download_count:desc"})
    assert resp.status_code == 200

    call_args = mock_client.post.call_args
    sent_body = call_args.kwargs.get("json") or call_args[1].get("json")
    assert sent_body["sort"] == ["download_count:desc"]


# --- build_meili_document unit tests ---

def test_build_meili_document_extracts_permissions():
    """build_meili_document extracts permission levels from manifest."""
    from unittest.mock import MagicMock
    from app.packages.service import build_meili_document

    pkg = MagicMock()
    pkg.id = "00000000-0000-0000-0000-000000000001"
    pkg.slug = "test-pack"
    pkg.name = "Test Pack"
    pkg.package_type = "toolpack"
    pkg.summary = "Test"
    pkg.description = ""
    pkg.publisher.display_name = "Pub"
    pkg.publisher.slug = "pub"
    pkg.publisher.trust_level = "verified"
    pkg.download_count = 0
    pkg.install_count = 0
    pkg.is_deprecated = False

    version = MagicMock()
    version.version_number = "1.0.0"
    version.runtime = "python"
    version.verification_status = None
    version.verification_score = None
    version.verification_tier = None
    version.published_at = None
    version.security_reviewed_at = None
    version.compatibility_reviewed_at = None
    version.manually_reviewed_at = None

    manifest = {
        "capabilities": {"tools": [{"capability_id": "test"}]},
        "tags": ["test"],
        "compatibility": {"frameworks": ["generic"]},
        "permissions": {
            "network": {"level": "unrestricted", "allowed_domains": []},
            "filesystem": {"level": "workspace_write"},
            "code_execution": {"level": "limited_subprocess"},
        },
        "connector": {"provider": "github", "auth_type": "oauth2"},
    }

    doc = build_meili_document(pkg, version, manifest)
    assert doc["network_level"] == "unrestricted"
    assert doc["filesystem_level"] == "workspace_write"
    assert doc["code_execution_level"] == "limited_subprocess"
    assert doc["has_connector"] is True


def test_build_meili_document_missing_permissions():
    """build_meili_document returns None for missing permission fields."""
    from unittest.mock import MagicMock
    from app.packages.service import build_meili_document

    pkg = MagicMock()
    pkg.id = "00000000-0000-0000-0000-000000000002"
    pkg.slug = "bare-pack"
    pkg.name = "Bare Pack"
    pkg.package_type = "toolpack"
    pkg.summary = "No permissions"
    pkg.description = ""
    pkg.publisher.display_name = "Pub"
    pkg.publisher.slug = "pub"
    pkg.publisher.trust_level = "verified"
    pkg.download_count = 0
    pkg.install_count = 0
    pkg.is_deprecated = False

    version = MagicMock()
    version.version_number = "1.0.0"
    version.runtime = "python"
    version.verification_status = None
    version.verification_score = None
    version.verification_tier = None
    version.published_at = None
    version.security_reviewed_at = None
    version.compatibility_reviewed_at = None
    version.manually_reviewed_at = None

    manifest = {
        "capabilities": {"tools": []},
        "tags": [],
        "compatibility": {"frameworks": []},
    }

    doc = build_meili_document(pkg, version, manifest)
    assert doc["network_level"] is None
    assert doc["filesystem_level"] is None
    assert doc["code_execution_level"] is None
    assert doc["has_connector"] is False
