"""Task #4: the MCP CLI commands send a dashboard API key the way the backend reads it.

A key created in the dashboard starts with ``ank_``. The backend authenticates it
from the ``X-API-Key`` header; a ``Bearer`` value is decoded as a login JWT, so an
API key sent as Bearer is always 401. ``mcp submit``, ``mcp status`` and
``mcp ownership challenge|verify`` sent it as Bearer. A real login JWT passed with
``--token`` still goes as Bearer. Placeholder keys only, httpx mocked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

API_KEY = "ank_" + "p" * 40
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0In0.c2ln"


def _resp(status: int, payload: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.is_success = 200 <= status < 300
    r.json.return_value = payload
    r.text = str(payload)
    return r


def _expect(headers: dict, credential: str):
    if credential.startswith("ank_"):
        assert headers.get("X-API-Key") == credential, headers
        assert "Authorization" not in headers, headers
    else:
        assert headers.get("Authorization") == f"Bearer {credential}", headers
        assert "X-API-Key" not in headers, headers


@pytest.mark.parametrize("credential", [API_KEY, JWT])
def test_status_sends_the_credential_the_backend_reads(credential):
    from agentnode_sdk.cli import mcp_status

    with patch("agentnode_sdk.cli.mcp_status.httpx.get",
               return_value=_resp(200, {"status": "pending"})) as get:
        assert mcp_status.cmd_mcp_status("sub-1", token=credential) == 0
    _expect(get.call_args.kwargs["headers"], credential)


@pytest.mark.parametrize("credential", [API_KEY, JWT])
def test_ownership_challenge_and_verify_send_the_credential_the_backend_reads(credential):
    from agentnode_sdk.cli import mcp_ownership

    ok = _resp(200, {"token": "t", "keyword": "k", "registry": "npm", "package_name": "p",
                     "expires_at": "2026-11-06T00:00:00+00:00", "instructions": "",
                     "verified": True, "status": "verified"})
    with patch("agentnode_sdk.cli.mcp_ownership.httpx.post", return_value=ok) as post:
        mcp_ownership.cmd_mcp_ownership_challenge("npm", "p", token=credential)
        _expect(post.call_args.kwargs["headers"], credential)
        mcp_ownership.cmd_mcp_ownership_verify("npm", "p", token=credential)
        _expect(post.call_args.kwargs["headers"], credential)


@pytest.mark.parametrize("credential", [API_KEY, JWT])
def test_submit_sends_the_credential_the_backend_reads(credential, tmp_path):
    from agentnode_sdk.cli import mcp_submit

    (tmp_path / "agentnode.yaml").write_text(
        "mcp_server:\n  npm_package: placeholder-mcp\n", encoding="utf-8"
    )
    report = SimpleNamespace(status="VALID", summary="ok", actions=[], to_dict=lambda: {})
    with patch("agentnode_sdk.cli.mcp_submit.verify_manifest", return_value=report), \
         patch("agentnode_sdk.cli.mcp_submit.httpx.post",
               return_value=_resp(201, {"id": "sub-1", "status": "pending"})) as post:
        mcp_submit.cmd_mcp_submit(str(tmp_path), token=credential)
    _expect(post.call_args.kwargs["headers"], credential)
