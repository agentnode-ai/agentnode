"""Integration tests for the resolution engine."""
import json
from unittest.mock import patch

import pytest

TEST_USER = {
    "email": "resolver@agentnode.dev",
    "username": "resolver",
    "password": "TestPass123!",
}

TEST_PUBLISHER = {
    "display_name": "Resolve Publisher",
    "slug": "resolve-pub",
}


def make_manifest(slug, capabilities, framework="generic", runtime="python"):
    return {
        "manifest_version": "0.1",
        "package_id": slug,
        "package_type": "toolpack",
        "name": slug.replace("-", " ").title(),
        "publisher": "resolve-pub",
        "version": "1.0.0",
        "summary": f"Package {slug} for testing.",
        "runtime": runtime,
        "install_mode": "package",
        "hosting_type": "agentnode_hosted",
        "entrypoint": f"{slug}.tool",
        "capabilities": {
            "tools": [
                {
                    "name": f"{cap}_tool",
                    "capability_id": cap,
                    "description": f"Tool for {cap}",
                    "input_schema": {"type": "object"},
                }
                for cap in capabilities
            ],
            "resources": [],
            "prompts": [],
        },
        "compatibility": {"frameworks": [framework], "python": ">=3.10"},
        "permissions": {
            "network": {"level": "none", "allowed_domains": []},
            "filesystem": {"level": "temp"},
            "code_execution": {"level": "none"},
            "data_access": {"level": "input_only"},
            "user_approval": {"required": "never"},
            "external_integrations": [],
        },
        "tags": ["test"],
        "categories": ["document-processing"],
        "dependencies": [],
        "security": {"signature": "", "provenance": {"source_repo": "", "commit": "", "build_system": ""}},
    }


async def setup_publisher(client):
    await client.post("/v1/auth/register", json=TEST_USER)
    login = await client.post("/v1/auth/login", json={
        "email": TEST_USER["email"],
        "password": TEST_USER["password"],
    })
    token = login.json()["access_token"]
    await client.post(
        "/v1/publishers",
        json=TEST_PUBLISHER,
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_resolve_single_capability(mock_meili, mock_s3, client):
    token = await setup_publisher(client)

    # Publish a package with pdf_extraction
    manifest = make_manifest("pdf-reader", ["pdf_extraction"])
    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(manifest)},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.post("/v1/resolve", json={
        "capabilities": ["pdf_extraction"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["results"][0]["slug"] == "pdf-reader"
    assert data["results"][0]["matched_capabilities"] == ["pdf_extraction"]
    assert data["results"][0]["score"] > 0


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_resolve_multiple_packages_ranked(mock_meili, mock_s3, client):
    token = await setup_publisher(client)

    # Package A: has pdf_extraction + web_search
    m1 = make_manifest("multi-tool", ["pdf_extraction", "web_search"])
    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(m1)},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Package B: has only pdf_extraction
    m2 = make_manifest("pdf-only", ["pdf_extraction"])
    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(m2)},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.post("/v1/resolve", json={
        "capabilities": ["pdf_extraction", "web_search"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    # multi-tool should rank higher (matches both capabilities)
    assert data["results"][0]["slug"] == "multi-tool"
    assert data["results"][0]["score"] > data["results"][1]["score"]


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_resolve_framework_filter(mock_meili, mock_s3, client):
    token = await setup_publisher(client)

    m1 = make_manifest("langchain-pdf", ["pdf_extraction"], framework="langchain")
    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(m1)},
        headers={"Authorization": f"Bearer {token}"},
    )

    m2 = make_manifest("generic-pdf", ["pdf_extraction"], framework="generic")
    await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(m2)},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.post("/v1/resolve", json={
        "capabilities": ["pdf_extraction"],
        "framework": "langchain",
    })
    data = resp.json()
    assert data["total"] == 2
    # langchain-pdf should rank higher for langchain framework
    assert data["results"][0]["slug"] == "langchain-pdf"


@pytest.mark.asyncio
async def test_resolve_no_match(client):
    resp = await client.post("/v1/resolve", json={
        "capabilities": ["nonexistent_capability"],
    })
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_resolve_empty_capabilities(client):
    resp = await client.post("/v1/resolve", json={
        "capabilities": [],
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_resolve_with_limit(mock_meili, mock_s3, client):
    token = await setup_publisher(client)

    # Use very distinct slugs to avoid typosquatting detection
    slugs = ["alpha-pdf-reader", "beta-doc-extractor", "gamma-text-parser"]
    for slug in slugs:
        m = make_manifest(slug, ["pdf_extraction"])
        await client.post(
            "/v1/packages/publish",
            data={"manifest": json.dumps(m)},
            headers={"Authorization": f"Bearer {token}"},
        )

    resp = await client.post("/v1/resolve", json={
        "capabilities": ["pdf_extraction"],
        "limit": 2,
    })
    data = resp.json()
    assert len(data["results"]) == 2
