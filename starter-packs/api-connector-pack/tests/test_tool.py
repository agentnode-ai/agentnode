"""Tests for api-connector-pack."""

import base64
from unittest.mock import MagicMock, patch

from api_connector_pack.tool import _build_auth_headers, _parse_response_body, run


# -- Pure helper tests (no mocking needed) --

def test_build_auth_headers_bearer():
    headers = _build_auth_headers("bearer", "tok123")
    assert headers == {"Authorization": "Bearer tok123"}


def test_build_auth_headers_api_key():
    headers = _build_auth_headers("api_key", "key456")
    assert headers == {"X-API-Key": "key456"}


def test_build_auth_headers_basic():
    headers = _build_auth_headers("basic", "user:pass")
    expected = base64.b64encode(b"user:pass").decode("ascii")
    assert headers == {"Authorization": f"Basic {expected}"}


def test_build_auth_headers_empty():
    assert _build_auth_headers("", "") == {}
    assert _build_auth_headers("bearer", "") == {}


def test_build_auth_headers_unknown_type():
    assert _build_auth_headers("oauth", "tok") == {}


# -- Input validation --

def test_missing_url():
    result = run(url="")
    assert result["success"] is False
    assert "url" in result["error"].lower()


def test_invalid_method():
    result = run(url="https://example.com", method="YEET")
    assert result["success"] is False
    assert "Invalid HTTP method" in result["error"]


# -- Mocked success flow --

def _mock_response(status_code=200, json_data=None, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = ""
    resp.headers = {"content-type": "application/json", **(headers or {})}
    return resp


@patch("api_connector_pack.tool.httpx.Client")
def test_get_success(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
    mock_client.request.return_value = _mock_response(200, {"ok": True})

    result = run(url="https://api.example.com/data", method="GET")
    assert result["success"] is True
    assert result["status_code"] == 200
    assert result["retries_used"] == 0


@patch("api_connector_pack.tool.httpx.Client")
def test_post_with_body(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
    mock_client.request.return_value = _mock_response(201, {"id": 1})

    result = run(url="https://api.example.com/items", method="POST", body={"name": "x"})
    assert result["success"] is True
    assert result["status_code"] == 201


# -- Error handling --

@patch("api_connector_pack.tool.httpx.Client")
def test_non_2xx_response(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
    mock_client.request.return_value = _mock_response(404, {"error": "not found"})

    result = run(url="https://api.example.com/missing", retries=0)
    assert result["success"] is False
    assert result["status_code"] == 404
