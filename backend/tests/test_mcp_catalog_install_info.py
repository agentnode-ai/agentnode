"""Task #3: what the catalog hands the SDK for an MCP package.

The SDK reads ``mcp_server.install`` (pre-install into a sealed volume),
``mcp_server.allowed_domains`` (sealed egress allowlist) and the tool list with
its input schema. Until now install-info dropped all of them, so the SDK's
pre-install and allowlist paths could never be reached from the catalog.
Synthetic packages only.
"""

from __future__ import annotations

import copy
from unittest.mock import patch

import pytest

from app.packages.validator import validate_manifest
from tests.test_install import MCP_MANIFEST, get_auth_token, publish_test_package


def _manifest(**mcp_extra):
    m = copy.deepcopy(MCP_MANIFEST)
    m["mcp_server"].update(mcp_extra)
    return m


async def _install_info(client, session, manifest):
    token = await get_auth_token(client, session)
    r = await publish_test_package(client, token, manifest=manifest)
    assert r.status_code == 201, r.text
    r = await client.get("/v1/packages/mcp-install-test/install-info")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_npm_install_descriptor_and_allowlist_reach_the_sdk(
    m1, m2, client, session
):
    data = await _install_info(
        client,
        session,
        _manifest(
            install={
                "manager": "npm",
                "package": "@modelcontextprotocol/server-test",
                "version": "1.0.0",
            },
            allowed_domains=["api.example.com"],
        ),
    )
    mcp = data["mcp_server"]
    assert mcp["install"] == {
        "manager": "npm",
        "package": "@modelcontextprotocol/server-test",
        "version": "1.0.0",
    }
    assert mcp["allowed_domains"] == ["api.example.com"]


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_pypi_package_reaches_the_sdk(m1, m2, client, session):
    data = await _install_info(
        client,
        session,
        _manifest(
            install={
                "manager": "pypi",
                "package": "mcp-server-time",
                "version": "0.6.2",
            },
            pypi_package="mcp-server-time",
        ),
    )
    mcp = data["mcp_server"]
    assert mcp["pypi_package"] == "mcp-server-time"
    assert mcp["install"]["manager"] == "pypi"


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_absent_fields_stay_absent(m1, m2, client, session):
    """A package that declares none of them gets null, not an invented default:
    no descriptor means metadata-only install, no allowlist means none sealed."""
    mcp = (await _install_info(client, session, _manifest()))["mcp_server"]
    assert mcp["install"] is None
    assert mcp["allowed_domains"] is None
    assert mcp["pypi_package"] is None


@pytest.mark.asyncio
@patch("app.packages.service.upload_artifact")
@patch("app.packages.service.sync_package_to_meilisearch")
async def test_tools_carry_their_input_schema(m1, m2, client, session):
    caps = (await _install_info(client, session, _manifest()))["capabilities"]
    tool = next(c for c in caps if c["name"] == "mcp_tool")
    assert tool["input_schema"] == {
        "type": "object",
        "properties": {"q": {"type": "string"}},
    }


@pytest.mark.parametrize(
    "extra, needle",
    [
        (
            {"install": {"manager": "git", "package": "x", "version": "1.0.0"}},
            "manager",
        ),
        (
            {"install": {"manager": "npm", "package": "x", "version": "^1.0.0"}},
            "version",
        ),
        (
            {"install": {"manager": "npm", "package": "x", "version": "latest"}},
            "version",
        ),
        (
            {"install": {"manager": "pypi", "package": "x", "version": ">=1.0"}},
            "version",
        ),
        (
            {
                "install": {
                    "manager": "npm",
                    "package": "x",
                    "version": "1.0.0",
                    "url": "y",
                }
            },
            "keys",
        ),
        ({"install": "npx foo"}, "install"),
        ({"allowed_domains": "api.example.com"}, "allowed_domains"),
        ({"allowed_domains": ["https://api.example.com"]}, "allowed_domains"),
        ({"allowed_domains": [""]}, "allowed_domains"),
        ({"pypi_package": 5}, "pypi_package"),
    ],
)
@pytest.mark.asyncio
async def test_validator_refuses_malformed_fields(extra, needle):
    _, errors, _ = await validate_manifest(_manifest(**extra))
    assert any("mcp_server" in e and needle in e for e in errors), errors


@pytest.mark.asyncio
async def test_validator_accepts_well_formed_fields():
    _, errors, _ = await validate_manifest(
        _manifest(
            install={"manager": "npm", "package": "@scope/pkg", "version": "2.3.4"},
            allowed_domains=["api.example.com", "Example.org"],
            pypi_package=None,
        )
    )
    assert not [e for e in errors if "mcp_server" in e], errors
