"""End-to-end test: Register -> Publisher -> Publish -> Search -> Resolve -> Install -> Download."""
import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

VALID_MANIFEST = {
    "manifest_version": "0.1",
    "package_id": "e2e-pdf-reader",
    "name": "E2E PDF Reader",
    "publisher": "e2e-publisher",
    "version": "1.0.0",
    "package_type": "toolpack",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "summary": "End-to-end test package for PDF extraction",
    "description": "A comprehensive PDF reader for E2E testing.",
    "entrypoint": "main:run",
    "tags": ["pdf", "extraction"],
    "categories": ["document-processing"],
    "capabilities": {
        "tools": [
            {
                "capability_id": "pdf_extraction",
                "name": "extract_pdf",
                "description": "Extract text from PDF files",
                "input_schema": {"type": "object", "properties": {"file": {"type": "string"}}},
            }
        ],
        "resources": [],
        "prompts": [],
    },
    "compatibility": {
        "frameworks": ["generic"],
        "python": ">=3.10",
    },
    "permissions": {
        "network": {"level": "none", "allowed_domains": []},
        "filesystem": {"level": "temp"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
        "external_integrations": [],
    },
    "security": {
        "provenance": {
            "source_repo": "https://github.com/e2e/pdf-reader",
            "commit": "abc123",
        }
    },
    "dependencies": [],
}


@pytest.mark.asyncio
async def test_full_e2e_flow(client):
    """Complete lifecycle: register, create publisher, publish, search, resolve, install, download."""

    # -- Step 1: Register user --
    resp = await client.post("/v1/auth/register", json={
        "email": "e2e@agentnode.net",
        "username": "e2euser",
        "password": "Str0ng!Pass#42",
    })
    assert resp.status_code == 201, f"Register failed: {resp.text}"

    # -- Step 2: Login --
    resp = await client.post("/v1/auth/login", json={
        "email": "e2e@agentnode.net",
        "password": "Str0ng!Pass#42",
    })
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # -- Step 3: Create publisher profile --
    resp = await client.post("/v1/publishers", json={
        "display_name": "E2E Publisher",
        "slug": "e2e-publisher",
        "bio": "End-to-end test publisher",
    }, headers=headers)
    assert resp.status_code == 201, f"Publisher create failed: {resp.text}"
    publisher_data = resp.json()
    assert publisher_data["slug"] == "e2e-publisher"

    # -- Step 4: Validate manifest --
    resp = await client.post("/v1/packages/validate", json={
        "manifest": VALID_MANIFEST,
    }, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["valid"] is True

    # -- Step 5: Publish package --
    with patch("app.packages.service.upload_artifact"):
        with patch("app.packages.service.sync_package_to_meilisearch", new_callable=AsyncMock):
            resp = await client.post("/v1/packages/publish", data={
                "manifest": json.dumps(VALID_MANIFEST),
            }, headers=headers)
    assert resp.status_code == 200, f"Publish failed: {resp.text}"
    publish_data = resp.json()
    assert publish_data["slug"] == "e2e-pdf-reader"
    assert publish_data["version"] == "1.0.0"
    assert publish_data["package_type"] == "toolpack"

    # -- Step 6: Get package detail --
    resp = await client.get("/v1/packages/e2e-pdf-reader")
    assert resp.status_code == 200
    pkg_data = resp.json()
    assert pkg_data["slug"] == "e2e-pdf-reader"
    assert pkg_data["name"] == "E2E PDF Reader"
    assert pkg_data["latest_version"]["version_number"] == "1.0.0"
    assert pkg_data["publisher"]["slug"] == "e2e-publisher"
    # Verify capabilities came through (nested in blocks)
    caps = pkg_data["blocks"]["capabilities"]
    assert len(caps) == 1
    assert caps[0]["capability_id"] == "pdf_extraction"

    # -- Step 7: Get package versions --
    resp = await client.get("/v1/packages/e2e-pdf-reader/versions")
    assert resp.status_code == 200
    versions = resp.json()["versions"]
    assert len(versions) == 1
    assert versions[0]["version_number"] == "1.0.0"

    # -- Step 8: Resolve capability --
    resp = await client.post("/v1/resolve", json={
        "capabilities": ["pdf_extraction"],
    })
    assert resp.status_code == 200
    resolve_data = resp.json()
    assert resolve_data["total"] >= 1
    slugs = [r["slug"] for r in resolve_data["results"]]
    assert "e2e-pdf-reader" in slugs

    # Verify score breakdown
    for result in resolve_data["results"]:
        if result["slug"] == "e2e-pdf-reader":
            assert result["score"] > 0
            assert "capability" in result["breakdown"]
            assert result["matched_capabilities"] == ["pdf_extraction"]
            break

    # -- Step 9: Install metadata --
    resp = await client.get("/v1/packages/e2e-pdf-reader/install")
    assert resp.status_code == 200
    install_data = resp.json()
    assert install_data["slug"] == "e2e-pdf-reader"
    assert install_data["version"] == "1.0.0"
    assert install_data["runtime"] == "python"
    assert install_data["entrypoint"] == "main:run"
    assert len(install_data["capabilities"]) == 1
    assert install_data["capabilities"][0]["capability_id"] == "pdf_extraction"
    assert install_data["permissions"]["filesystem_level"] == "temp"

    # -- Step 10: Track download --
    resp = await client.post("/v1/packages/e2e-pdf-reader/download")
    assert resp.status_code == 200
    dl_data = resp.json()
    assert dl_data["slug"] == "e2e-pdf-reader"
    assert dl_data["version"] == "1.0.0"
    assert dl_data["download_count"] == 1

    # Download again from same IP — deduplicated, count stays at 1
    resp = await client.post("/v1/packages/e2e-pdf-reader/download")
    assert resp.status_code == 200
    assert resp.json()["download_count"] == 1


@pytest.mark.asyncio
async def test_publish_then_deprecate_flow(client):
    """Publish a package, then deprecate it, verify it is excluded from resolution."""

    # Setup: register, login, create publisher
    await client.post("/v1/auth/register", json={
        "email": "depr@agentnode.net", "username": "depruser", "password": "Str0ng!Pass#42",
    })
    resp = await client.post("/v1/auth/login", json={
        "email": "depr@agentnode.net", "password": "Str0ng!Pass#42",
    })
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    await client.post("/v1/publishers", json={
        "display_name": "Deprecation Tester", "slug": "depr-tester",
    }, headers=headers)

    # Publish
    manifest = {**VALID_MANIFEST, "package_id": "depr-pdf-tool", "name": "Depr PDF Tool", "publisher": "depr-tester"}
    with patch("app.packages.service.upload_artifact"):
        with patch("app.packages.service.sync_package_to_meilisearch", new_callable=AsyncMock):
            resp = await client.post("/v1/packages/publish", data={
                "manifest": json.dumps(manifest),
            }, headers=headers)
    assert resp.status_code == 200

    # Verify it resolves
    resp = await client.post("/v1/resolve", json={"capabilities": ["pdf_extraction"]})
    slugs = [r["slug"] for r in resp.json()["results"]]
    assert "depr-pdf-tool" in slugs

    # Deprecate
    resp = await client.post("/v1/packages/depr-pdf-tool/deprecate", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["deprecated"] is True

    # Verify it no longer resolves
    resp = await client.post("/v1/resolve", json={"capabilities": ["pdf_extraction"]})
    slugs = [r["slug"] for r in resp.json()["results"]]
    assert "depr-pdf-tool" not in slugs


@pytest.mark.asyncio
async def test_publish_then_yank_version_flow(client):
    """Publish two versions, yank the latest, verify install falls back to previous."""

    # Setup
    await client.post("/v1/auth/register", json={
        "email": "yank@agentnode.net", "username": "yankuser", "password": "Str0ng!Pass#42",
    })
    resp = await client.post("/v1/auth/login", json={
        "email": "yank@agentnode.net", "password": "Str0ng!Pass#42",
    })
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    await client.post("/v1/publishers", json={
        "display_name": "Yank Tester", "slug": "yank-tester",
    }, headers=headers)

    # Publish v1.0.0
    manifest_v1 = {**VALID_MANIFEST, "package_id": "yank-pdf-tool", "name": "Yank PDF Tool", "version": "1.0.0", "publisher": "yank-tester"}
    with patch("app.packages.service.upload_artifact"):
        with patch("app.packages.service.sync_package_to_meilisearch", new_callable=AsyncMock):
            resp = await client.post("/v1/packages/publish", data={
                "manifest": json.dumps(manifest_v1),
            }, headers=headers)
    assert resp.status_code == 200

    # Publish v2.0.0
    manifest_v2 = {**manifest_v1, "version": "2.0.0"}
    with patch("app.packages.service.upload_artifact"):
        with patch("app.packages.service.sync_package_to_meilisearch", new_callable=AsyncMock):
            resp = await client.post("/v1/packages/publish", data={
                "manifest": json.dumps(manifest_v2),
            }, headers=headers)
    assert resp.status_code == 200

    # Install metadata should show 2.0.0
    resp = await client.get("/v1/packages/yank-pdf-tool/install")
    assert resp.json()["version"] == "2.0.0"

    # Yank v2.0.0
    resp = await client.post("/v1/packages/yank-pdf-tool/versions/2.0.0/yank", headers=headers)
    assert resp.status_code == 200

    # Install metadata should now fall back to 1.0.0
    resp = await client.get("/v1/packages/yank-pdf-tool/install")
    assert resp.json()["version"] == "1.0.0"
