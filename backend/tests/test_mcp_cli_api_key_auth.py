"""Task #4, server side: how the MCP endpoints read a dashboard API key.

Documents the contract the CLI has to meet: an ``ank_`` key is accepted in
``X-API-Key`` and refused as ``Authorization: Bearer`` (Bearer means a login JWT).
Synthetic account and key.
"""

from __future__ import annotations

import pytest

from tests.conftest import setup_publisher_user


async def _api_key(client) -> str:
    token, _ = await setup_publisher_user(
        client,
        "cli-key@agentnode.dev",
        "clikey",
        "Placeholder-Pass-1!",
        "cli-key-pub",
        "CLI Key",
    )
    r = await client.post(
        "/v1/auth/api-keys",
        json={"label": "cli"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["key"]


@pytest.mark.asyncio
async def test_api_key_in_x_api_key_reaches_the_mcp_endpoint(client):
    key = await _api_key(client)
    r = await client.get(
        "/v1/mcp/submissions/00000000-0000-0000-0000-000000000000",
        headers={"X-API-Key": key},
    )
    assert r.status_code != 401, (
        r.text
    )  # authenticated; the submission simply does not exist


@pytest.mark.asyncio
async def test_api_key_as_bearer_is_refused(client):
    key = await _api_key(client)
    r = await client.get(
        "/v1/mcp/submissions/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 401, r.text
