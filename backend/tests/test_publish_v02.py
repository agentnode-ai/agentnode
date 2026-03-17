"""Integration tests for v0.2 publish flow — entrypoint stored per tool, API responses correct."""
import json
from unittest.mock import patch

import pytest


TEST_USER = {
    "email": "v02user@agentnode.dev",
    "username": "v02user",
    "password": "TestPass123!",
}

TEST_PUBLISHER = {
    "display_name": "V02 Publisher",
    "slug": "v02-publisher",
}

V02_MULTI_TOOL_MANIFEST = {
    "manifest_version": "0.2",
    "package_id": "v02-multi-pack",
    "package_type": "toolpack",
    "name": "V02 Multi Pack",
    "publisher": "v02-publisher",
    "version": "1.0.0",
    "summary": "A v0.2 multi-tool pack for testing.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "v02_multi_pack.tool",
    "capabilities": {
        "tools": [
            {
                "name": "describe",
                "capability_id": "pdf_extraction",
                "description": "Describe data",
                "entrypoint": "v02_multi_pack.tool:describe",
                "input_schema": {
                    "type": "object",
                    "properties": {"file_path": {"type": "string"}},
                    "required": ["file_path"],
                },
            },
            {
                "name": "filter",
                "capability_id": "web_search",
                "description": "Filter data",
                "entrypoint": "v02_multi_pack.tool:filter_rows",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["column", "value"],
                },
            },
        ],
        "resources": [],
        "prompts": [],
    },
    "compatibility": {"frameworks": ["generic"]},
    "permissions": {
        "network": {"level": "none", "allowed_domains": []},
        "filesystem": {"level": "none"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
    },
    "tags": ["test"],
    "categories": ["data"],
    "dependencies": [],
    "security": {"signature": "", "provenance": {"source_repo": "", "commit": "", "build_system": ""}},
}

V02_SINGLE_TOOL_MANIFEST = {
    **V02_MULTI_TOOL_MANIFEST,
    "package_id": "v02-single-pack",
    "name": "V02 Single Pack",
    "version": "1.0.0",
    "summary": "A v0.2 single-tool pack.",
    "entrypoint": "v02_single_pack.tool",
    "capabilities": {
        "tools": [{
            "name": "do_thing",
            "capability_id": "pdf_extraction",
            "description": "Does the thing",
            "entrypoint": "v02_single_pack.tool:do_thing",
        }],
        "resources": [],
        "prompts": [],
    },
}

V01_MANIFEST = {
    "manifest_version": "0.1",
    "package_id": "v01-compat-pack",
    "package_type": "toolpack",
    "name": "V01 Compat Pack",
    "publisher": "v02-publisher",
    "version": "1.0.0",
    "summary": "A v0.1 pack to test backward compat.",
    "runtime": "python",
    "install_mode": "package",
    "hosting_type": "agentnode_hosted",
    "entrypoint": "v01_compat_pack.tool",
    "capabilities": {
        "tools": [{
            "name": "test_tool",
            "capability_id": "pdf_extraction",
            "description": "Test tool",
        }],
        "resources": [],
        "prompts": [],
    },
    "compatibility": {"frameworks": ["generic"]},
    "permissions": {
        "network": {"level": "none", "allowed_domains": []},
        "filesystem": {"level": "none"},
        "code_execution": {"level": "none"},
        "data_access": {"level": "input_only"},
        "user_approval": {"required": "never"},
    },
    "tags": ["test"],
    "categories": ["data"],
    "dependencies": [],
    "security": {"signature": "", "provenance": {"source_repo": "", "commit": "", "build_system": ""}},
}


async def get_auth_token(client) -> str:
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


async def publish(client, token, manifest):
    return await client.post(
        "/v1/packages/publish",
        data={"manifest": json.dumps(manifest)},
        headers={"Authorization": f"Bearer {token}"},
    )


# ---------------------------------------------------------------------------
# Publish tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_publish_v02_multi_tool(mock_meili, mock_s3, client):
    """Publishing a v0.2 multi-tool pack should succeed."""
    token = await get_auth_token(client)
    resp = await publish(client, token, V02_MULTI_TOOL_MANIFEST)
    assert resp.status_code == 200, f"Publish failed: {resp.json()}"
    data = resp.json()
    assert data["slug"] == "v02-multi-pack"
    assert data["version"] == "1.0.0"


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_publish_v02_single_tool(mock_meili, mock_s3, client):
    """Publishing a v0.2 single-tool pack should succeed."""
    token = await get_auth_token(client)
    resp = await publish(client, token, V02_SINGLE_TOOL_MANIFEST)
    assert resp.status_code == 200, f"Publish failed: {resp.json()}"


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_v01_still_publishes(mock_meili, mock_s3, client):
    """v0.1 manifests must still publish successfully."""
    token = await get_auth_token(client)
    resp = await publish(client, token, V01_MANIFEST)
    assert resp.status_code == 200, f"v0.1 publish failed: {resp.json()}"


# ---------------------------------------------------------------------------
# Package detail — capabilities include entrypoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_package_detail_v02_has_entrypoints(mock_meili, mock_s3, client):
    """GET /v1/packages/{slug} should include entrypoint per capability for v0.2."""
    token = await get_auth_token(client)
    await publish(client, token, V02_MULTI_TOOL_MANIFEST)

    resp = await client.get("/v1/packages/v02-multi-pack")
    assert resp.status_code == 200
    data = resp.json()

    caps = data["blocks"]["capabilities"]
    assert len(caps) == 2

    # Find describe tool
    describe_cap = next(c for c in caps if c["name"] == "describe")
    assert describe_cap["entrypoint"] == "v02_multi_pack.tool:describe"
    assert describe_cap["input_schema"] is not None
    assert describe_cap["input_schema"]["type"] == "object"

    # Find filter tool
    filter_cap = next(c for c in caps if c["name"] == "filter")
    assert filter_cap["entrypoint"] == "v02_multi_pack.tool:filter_rows"


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_package_detail_v01_null_entrypoints(mock_meili, mock_s3, client):
    """GET /v1/packages/{slug} should have null entrypoint for v0.1 capabilities."""
    token = await get_auth_token(client)
    await publish(client, token, V01_MANIFEST)

    resp = await client.get("/v1/packages/v01-compat-pack")
    assert resp.status_code == 200
    data = resp.json()

    caps = data["blocks"]["capabilities"]
    assert len(caps) == 1
    assert caps[0]["entrypoint"] is None


# ---------------------------------------------------------------------------
# Install-info — capabilities include entrypoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_install_info_v02_has_entrypoints(mock_meili, mock_s3, client):
    """GET /v1/packages/{slug}/install-info should include tool entrypoints."""
    token = await get_auth_token(client)
    await publish(client, token, V02_MULTI_TOOL_MANIFEST)

    resp = await client.get("/v1/packages/v02-multi-pack/install")
    assert resp.status_code == 200
    data = resp.json()

    assert data["entrypoint"] == "v02_multi_pack.tool"
    caps = data["capabilities"]
    assert len(caps) == 2

    describe_cap = next(c for c in caps if c["name"] == "describe")
    assert describe_cap["entrypoint"] == "v02_multi_pack.tool:describe"

    filter_cap = next(c for c in caps if c["name"] == "filter")
    assert filter_cap["entrypoint"] == "v02_multi_pack.tool:filter_rows"


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_install_info_v01_null_entrypoints(mock_meili, mock_s3, client):
    """GET /v1/packages/{slug}/install-info — v0.1 capabilities have null entrypoint."""
    token = await get_auth_token(client)
    await publish(client, token, V01_MANIFEST)

    resp = await client.get("/v1/packages/v01-compat-pack/install")
    assert resp.status_code == 200
    data = resp.json()

    assert data["entrypoint"] == "v01_compat_pack.tool"
    assert len(data["capabilities"]) == 1
    assert data["capabilities"][0]["entrypoint"] is None


# ---------------------------------------------------------------------------
# Install response — tools list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_install_v02_returns_tools(mock_meili, mock_s3, client):
    """POST /v1/packages/{slug}/install should return tools list for v0.2."""
    token = await get_auth_token(client)
    await publish(client, token, V02_MULTI_TOOL_MANIFEST)

    resp = await client.post(
        "/v1/packages/v02-multi-pack/install",
        json={"source": "cli", "event_type": "install"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert "tools" in data
    assert len(data["tools"]) == 2
    tool_names = {t["name"] for t in data["tools"]}
    assert "describe" in tool_names
    assert "filter" in tool_names

    describe_tool = next(t for t in data["tools"] if t["name"] == "describe")
    assert describe_tool["entrypoint"] == "v02_multi_pack.tool:describe"
    assert describe_tool["capability_id"] == "pdf_extraction"


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_install_v01_returns_empty_tools(mock_meili, mock_s3, client):
    """POST /v1/packages/{slug}/install should return empty tools for v0.1."""
    token = await get_auth_token(client)
    await publish(client, token, V01_MANIFEST)

    resp = await client.post(
        "/v1/packages/v01-compat-pack/install",
        json={"source": "cli", "event_type": "install"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert "tools" in data
    assert data["tools"] == []


# ---------------------------------------------------------------------------
# Normalization in publish flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_publish_compact_v02_with_defaults(mock_meili, mock_s3, client):
    """A compact v0.2 manifest (missing runtime/permissions/etc) should publish
    after normalization fills in defaults."""
    token = await get_auth_token(client)

    compact = {
        "manifest_version": "0.2",
        "package_id": "compact-v02",
        "package_type": "toolpack",
        "name": "Compact V02",
        "publisher": "v02-publisher",
        "version": "1.0.0",
        "summary": "Compact v0.2 manifest.",
        "entrypoint": "compact_v02.tool",
        "capabilities": {
            "tools": [{
                "name": "do_it",
                "capability_id": "pdf_extraction",
                "description": "Does it",
                "entrypoint": "compact_v02.tool:do_it",
            }],
            "resources": [],
            "prompts": [],
        },
        "compatibility": {"frameworks": ["generic"]},
        "tags": ["test"],
        "categories": ["data"],
        "dependencies": [],
        # runtime, install_mode, hosting_type, permissions all missing — defaults should apply
    }

    resp = await publish(client, token, compact)
    assert resp.status_code == 200, f"Compact publish failed: {resp.json()}"

    # Verify it's accessible
    detail = await client.get("/v1/packages/compact-v02")
    assert detail.status_code == 200
    assert detail.json()["name"] == "Compact V02"


# ---------------------------------------------------------------------------
# Validation rejection tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_publish_v02_multi_tool_missing_entrypoint_rejected(mock_meili, mock_s3, client):
    """v0.2 multi-tool pack with missing tool entrypoint must be rejected."""
    token = await get_auth_token(client)

    bad = {
        **V02_MULTI_TOOL_MANIFEST,
        "package_id": "bad-v02-pack",
        "capabilities": {
            "tools": [
                {"name": "a", "capability_id": "pdf_extraction", "description": "A",
                 "entrypoint": "bad.tool:a"},
                {"name": "b", "capability_id": "web_search", "description": "B"},
                # tool "b" missing entrypoint
            ],
            "resources": [],
            "prompts": [],
        },
    }

    resp = await publish(client, token, bad)
    assert resp.status_code == 422
    data = resp.json()
    assert any("entrypoint" in str(d).lower() for d in data.get("error", {}).get("details", []))
