"""Tests for the `agentnode mcp ownership challenge|verify` CLI (Slice 2b-2 UX).

The two verbs drive the live publish-challenge endpoints. These tests mock httpx
at the CLI boundary — no real network / registry call — and assert the parser
wiring, auth/URL handling, human output, and every error case. They also pin the
honesty line: the CLI never claims auto-publish.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from agentnode_sdk.cli import mcp_ownership
from agentnode_sdk.cli.main import main


def _resp(status_code: int, payload: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = payload
    r.text = str(payload)
    return r


# --- parser wiring -----------------------------------------------------------


def test_parser_routes_challenge(monkeypatch):
    monkeypatch.setenv("AGENTNODE_API_KEY", "key-1")
    with patch(
        "agentnode_sdk.cli.mcp_ownership.cmd_mcp_ownership_challenge", return_value=0
    ) as fn:
        rc = main(["mcp", "ownership", "challenge", "--registry", "npm", "my-mcp"])
    assert rc == 0
    fn.assert_called_once()
    args, kwargs = fn.call_args
    assert args[0] == "npm" and args[1] == "my-mcp"


def test_parser_routes_verify(monkeypatch):
    monkeypatch.setenv("AGENTNODE_API_KEY", "key-1")
    with patch(
        "agentnode_sdk.cli.mcp_ownership.cmd_mcp_ownership_verify", return_value=0
    ) as fn:
        rc = main(["mcp", "ownership", "verify", "--registry", "pypi", "my-pkg"])
    assert rc == 0
    args, _ = fn.call_args
    assert args[0] == "pypi" and args[1] == "my-pkg"


def test_invalid_registry_rejected_by_parser(monkeypatch):
    # argparse choices=[npm,pypi] -> exits before the handler runs.
    monkeypatch.setenv("AGENTNODE_API_KEY", "key-1")
    try:
        main(["mcp", "ownership", "challenge", "--registry", "cargo", "x"])
        raise AssertionError("expected SystemExit for an invalid registry")
    except SystemExit as e:
        assert e.code == 2


# --- auth / URL --------------------------------------------------------------


def test_challenge_requires_api_key(monkeypatch, capsys):
    monkeypatch.delenv("AGENTNODE_API_KEY", raising=False)
    rc = mcp_ownership.cmd_mcp_ownership_challenge("npm", "x", token=None)
    assert rc == 1
    assert "api key" in capsys.readouterr().err.lower()


def test_challenge_uses_api_url_and_bearer(monkeypatch):
    monkeypatch.setenv("AGENTNODE_API_URL", "https://api.example.test")
    ok = _resp(
        200,
        {
            "token": "tok-secret",
            "keyword": "agentnode-ownership-tok-secret",
            "registry": "npm",
            "package_name": "my-mcp",
            "expires_at": "2026-08-09T10:00:00+00:00",
            "instructions": "...",
        },
    )
    with patch("agentnode_sdk.cli.mcp_ownership.httpx.post", return_value=ok) as post:
        rc = mcp_ownership.cmd_mcp_ownership_challenge("npm", "my-mcp", token="key-9")
    assert rc == 0
    url = post.call_args.args[0]
    assert url == "https://api.example.test/v1/mcp/ownership/challenge"
    headers = post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer key-9"
    body = post.call_args.kwargs["json"]
    assert body == {"registry": "npm", "package_name": "my-mcp"}


# --- challenge output --------------------------------------------------------


def test_challenge_success_shows_token_keyword_instructions(capsys):
    ok = _resp(
        200,
        {
            "token": "tok-secret",
            "keyword": "agentnode-ownership-tok-secret",
            "registry": "npm",
            "package_name": "my-mcp",
            "expires_at": "2026-08-09T10:00:00+00:00",
            "instructions": "...",
        },
    )
    with patch("agentnode_sdk.cli.mcp_ownership.httpx.post", return_value=ok):
        rc = mcp_ownership.cmd_mcp_ownership_challenge("npm", "my-mcp", token="key-9")
    assert rc == 0
    out = capsys.readouterr().out
    assert "tok-secret" in out  # token shown
    assert "agentnode-ownership-tok-secret" in out  # keyword shown
    assert "shown once" in out.lower()
    assert "verify --registry npm my-mcp" in out
    assert "2026-08-09" in out  # expiry (date only)
    # honesty line, no auto-publish claim
    assert "review-gated" in out.lower()
    for bad in ("auto-publish", "publishes automatically", "goes live after verify"):
        assert bad not in out.lower()


def test_challenge_json_output(capsys):
    ok = _resp(
        200,
        {"token": "t", "keyword": "agentnode-ownership-t", "registry": "npm",
         "package_name": "p", "expires_at": "2026-08-09T00:00:00+00:00", "instructions": "x"},
    )
    with patch("agentnode_sdk.cli.mcp_ownership.httpx.post", return_value=ok):
        rc = mcp_ownership.cmd_mcp_ownership_challenge(
            "npm", "p", token="k", json_output=True
        )
    assert rc == 0
    import json

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["keyword"] == "agentnode-ownership-t"


# --- verify output -----------------------------------------------------------


def test_verify_success_is_strong_and_exit_zero(capsys):
    ok = _resp(
        200,
        {
            "verified": True,
            "status": "verified",
            "message": "Ownership verified via a published version — strong evidence.",
            "version": "1.1.0",
        },
    )
    with patch("agentnode_sdk.cli.mcp_ownership.httpx.post", return_value=ok):
        rc = mcp_ownership.cmd_mcp_ownership_verify("npm", "my-mcp", token="key-9")
    assert rc == 0
    out = capsys.readouterr().out
    assert "verified" in out.lower()
    assert "1.1.0" in out
    assert "strong ownership evidence" in out.lower()
    assert "review-gated" in out.lower()
    assert "auto-publish" not in out.lower()


def test_verify_pending_tells_user_to_publish_and_retry(capsys):
    pending = _resp(
        200,
        {
            "verified": False,
            "status": "pending",
            "message": (
                "Token not found in the latest published version's keywords yet. "
                "Publish a version with the keyword, then verify again."
            ),
        },
    )
    with patch("agentnode_sdk.cli.mcp_ownership.httpx.post", return_value=pending):
        rc = mcp_ownership.cmd_mcp_ownership_verify("npm", "my-mcp", token="key-9")
    # not verified -> non-zero, so CI can gate on it
    assert rc == 1
    out = capsys.readouterr().out
    assert "verify again" in out.lower()
    assert "not verified yet" in out.lower()


# --- error cases -------------------------------------------------------------


def test_verify_no_challenge_404(capsys):
    err = _resp(
        404, {"code": "MCP_NO_CHALLENGE", "message": "No publish-challenge to verify — issue one first."}
    )
    with patch("agentnode_sdk.cli.mcp_ownership.httpx.post", return_value=err):
        rc = mcp_ownership.cmd_mcp_ownership_verify("npm", "x", token="k")
    assert rc == 1
    assert "issue one first" in capsys.readouterr().err.lower()


def test_verify_package_not_found_404(capsys):
    err = _resp(
        404, {"code": "MCP_PACKAGE_NOT_FOUND", "message": "Package 'x' not found on npm."}
    )
    with patch("agentnode_sdk.cli.mcp_ownership.httpx.post", return_value=err):
        rc = mcp_ownership.cmd_mcp_ownership_verify("npm", "x", token="k")
    assert rc == 1
    assert "not found on npm" in capsys.readouterr().err.lower()


def test_verify_expired_400(capsys):
    err = _resp(
        400, {"code": "MCP_CHALLENGE_EXPIRED", "message": "The challenge has expired — issue a new one."}
    )
    with patch("agentnode_sdk.cli.mcp_ownership.httpx.post", return_value=err):
        rc = mcp_ownership.cmd_mcp_ownership_verify("npm", "x", token="k")
    assert rc == 1
    assert "expired" in capsys.readouterr().err.lower()


def test_verify_registry_unavailable_503(capsys):
    err = _resp(
        503,
        {"code": "MCP_REGISTRY_UNAVAILABLE", "message": "The registry is temporarily unavailable — try verify again shortly."},
    )
    with patch("agentnode_sdk.cli.mcp_ownership.httpx.post", return_value=err):
        rc = mcp_ownership.cmd_mcp_ownership_verify("npm", "x", token="k")
    assert rc == 1
    assert "temporarily unavailable" in capsys.readouterr().err.lower()


def test_challenge_401_auth_failed(capsys):
    err = _resp(401, {"code": "UNAUTHORIZED", "message": "bad token"})
    with patch("agentnode_sdk.cli.mcp_ownership.httpx.post", return_value=err):
        rc = mcp_ownership.cmd_mcp_ownership_challenge("npm", "x", token="k")
    assert rc == 1
    assert "authentication failed" in capsys.readouterr().err.lower()


def test_challenge_403_needs_publisher(capsys):
    err = _resp(403, {"code": "PUBLISHER_REQUIRED", "message": "publisher profile required"})
    with patch("agentnode_sdk.cli.mcp_ownership.httpx.post", return_value=err):
        rc = mcp_ownership.cmd_mcp_ownership_challenge("npm", "x", token="k")
    assert rc == 1
    assert "publisher profile" in capsys.readouterr().err.lower()
