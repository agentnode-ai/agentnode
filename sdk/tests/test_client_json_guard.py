"""P1-SDK3: Validate success-path JSON responses are dicts, not arrays/scalars."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from agentnode_sdk import AgentNodeClient
from agentnode_sdk.exceptions import AgentNodeError

BASE = "https://api.agentnode.net"


class TestSyncClientJsonGuard:
    """AgentNodeClient._request rejects non-dict JSON on success path."""

    @respx.mock
    def test_array_response_raises(self):
        respx.get(f"{BASE}/v1/test").mock(
            return_value=httpx.Response(
                200,
                content=json.dumps([1, 2, 3]),
                headers={"content-type": "application/json"},
            )
        )
        with AgentNodeClient() as client:
            with pytest.raises(AgentNodeError, match="Expected JSON object, got list"):
                client._request("GET", "/test")

    @respx.mock
    def test_string_response_raises(self):
        respx.get(f"{BASE}/v1/test").mock(
            return_value=httpx.Response(
                200,
                content=json.dumps("hello"),
                headers={"content-type": "application/json"},
            )
        )
        with AgentNodeClient() as client:
            with pytest.raises(AgentNodeError, match="Expected JSON object, got str"):
                client._request("GET", "/test")

    @respx.mock
    def test_null_response_raises(self):
        respx.get(f"{BASE}/v1/test").mock(
            return_value=httpx.Response(
                200,
                content=json.dumps(None),
                headers={"content-type": "application/json"},
            )
        )
        with AgentNodeClient() as client:
            with pytest.raises(AgentNodeError, match="Expected JSON object, got NoneType"):
                client._request("GET", "/test")

    @respx.mock
    def test_int_response_raises(self):
        respx.get(f"{BASE}/v1/test").mock(
            return_value=httpx.Response(
                200,
                content=json.dumps(42),
                headers={"content-type": "application/json"},
            )
        )
        with AgentNodeClient() as client:
            with pytest.raises(AgentNodeError, match="Expected JSON object, got int"):
                client._request("GET", "/test")

    @respx.mock
    def test_dict_response_passes(self):
        respx.get(f"{BASE}/v1/test").mock(
            return_value=httpx.Response(
                200,
                json={"ok": True},
            )
        )
        with AgentNodeClient() as client:
            result = client._request("GET", "/test")
            assert result == {"ok": True}

    @respx.mock
    def test_empty_dict_response_passes(self):
        respx.get(f"{BASE}/v1/test").mock(
            return_value=httpx.Response(
                200,
                json={},
            )
        )
        with AgentNodeClient() as client:
            result = client._request("GET", "/test")
            assert result == {}


class TestAsyncClientJsonGuard:
    """AgentNode._handle rejects non-dict JSON on success path."""

    def test_array_response_raises(self):
        from agentnode_sdk.client import AgentNode

        client = AgentNode.__new__(AgentNode)
        resp = httpx.Response(
            200,
            content=json.dumps([1, 2, 3]),
            headers={"content-type": "application/json"},
        )
        with pytest.raises(AgentNodeError, match="Expected JSON object, got list"):
            client._handle(resp)

    def test_string_response_raises(self):
        from agentnode_sdk.client import AgentNode

        client = AgentNode.__new__(AgentNode)
        resp = httpx.Response(
            200,
            content=json.dumps("hello"),
            headers={"content-type": "application/json"},
        )
        with pytest.raises(AgentNodeError, match="Expected JSON object, got str"):
            client._handle(resp)

    def test_null_response_raises(self):
        from agentnode_sdk.client import AgentNode

        client = AgentNode.__new__(AgentNode)
        resp = httpx.Response(
            200,
            content=json.dumps(None),
            headers={"content-type": "application/json"},
        )
        with pytest.raises(AgentNodeError, match="Expected JSON object, got NoneType"):
            client._handle(resp)

    def test_dict_response_passes(self):
        from agentnode_sdk.client import AgentNode

        client = AgentNode.__new__(AgentNode)
        resp = httpx.Response(
            200,
            json={"ok": True},
        )
        result = client._handle(resp)
        assert result == {"ok": True}

    def test_error_path_still_works_with_non_dict(self):
        """Error path (status >= 400) must not crash on non-dict JSON body."""
        from agentnode_sdk.client import AgentNode

        client = AgentNode.__new__(AgentNode)
        resp = httpx.Response(
            500,
            content=json.dumps([{"error": "bad"}]),
            headers={"content-type": "application/json"},
        )
        with pytest.raises(AgentNodeError):
            client._handle(resp)
