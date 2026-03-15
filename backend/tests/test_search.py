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
        },
    ],
    "estimatedTotalHits": 1,
}


def _mock_httpx_client(response_data, status_code=200):
    """Create a mock httpx.AsyncClient that works as async context manager."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = response_data

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)

    return mock_client


@pytest.mark.asyncio
@patch("app.search.router.httpx.AsyncClient")
async def test_search_basic(mock_cls, client):
    mock_cls.return_value.__aenter__ = AsyncMock(
        return_value=_mock_httpx_client(MOCK_MEILI_RESPONSE)
    )
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    resp = await client.get("/v1/search?q=pdf")
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "pdf"
    assert data["total"] == 1
    assert len(data["hits"]) == 1
    assert data["hits"][0]["slug"] == "pdf-reader-pack"
    assert data["hits"][0]["capability_ids"] == ["pdf_extraction"]


@pytest.mark.asyncio
@patch("app.search.router.httpx.AsyncClient")
async def test_search_with_filters(mock_cls, client):
    inner = _mock_httpx_client({"hits": [], "estimatedTotalHits": 0})
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=inner)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    resp = await client.get(
        "/v1/search?q=pdf&package_type=toolpack&capability=pdf_extraction&framework=langchain"
    )
    assert resp.status_code == 200

    call_args = inner.post.call_args
    sent_body = call_args.kwargs.get("json") or call_args[1].get("json")
    filters = sent_body["filter"]
    assert 'package_type = "toolpack"' in filters
    assert 'capability_ids = "pdf_extraction"' in filters
    assert 'frameworks = "langchain"' in filters


@pytest.mark.asyncio
@patch("app.search.router.httpx.AsyncClient")
async def test_search_empty_query(mock_cls, client):
    inner = _mock_httpx_client({"hits": [], "estimatedTotalHits": 0})
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=inner)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    resp = await client.get("/v1/search")
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == ""


@pytest.mark.asyncio
@patch("app.search.router.httpx.AsyncClient")
async def test_search_meili_failure_returns_empty(mock_cls, client):
    inner = AsyncMock()
    inner.post = AsyncMock(side_effect=Exception("Connection refused"))
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=inner)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    resp = await client.get("/v1/search?q=pdf")
    assert resp.status_code == 200
    data = resp.json()
    assert data["hits"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
@patch("app.search.router.httpx.AsyncClient")
async def test_search_with_sort(mock_cls, client):
    inner = _mock_httpx_client({"hits": [], "estimatedTotalHits": 0})
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=inner)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

    resp = await client.get("/v1/search?q=test&sort=download_count:desc")
    assert resp.status_code == 200

    call_args = inner.post.call_args
    sent_body = call_args.kwargs.get("json") or call_args[1].get("json")
    assert sent_body["sort"] == ["download_count:desc"]
