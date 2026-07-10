"""Slice 2b-2 — publish-challenge ownership (first real STRONG mechanism).

Prove package control by publishing a version whose keywords carry a server-
issued token. On success the claim becomes STRONG + verified. Still activates NO
auto-publish (sandbox_smoke remains a future blocker); MCP stays review-gated.

Registry is mocked at the _fetch_npm/_fetch_pypi boundary — no network.
"""

from __future__ import annotations

import pytest

from app.mcp import ownership as own
from tests.conftest import setup_publisher_user


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- pure helpers ------------------------------------------------------------


def test_keyword_and_hash_roundtrip():
    t = own.new_challenge_token()
    kw = own.challenge_keyword(t)
    assert kw == f"agentnode-ownership-{t}"
    assert own.hash_token(t) == own.hash_token(t)
    assert own.hash_token(t) != own.hash_token(own.new_challenge_token())


def test_find_challenge_match_npm():
    t = own.new_challenge_token()
    h = own.hash_token(t)
    data = {
        "dist-tags": {"latest": "1.2.3"},
        "versions": {"1.2.3": {"keywords": ["mcp", own.challenge_keyword(t)]}},
    }
    m = own.find_challenge_match("npm", data, h)
    assert m["found"] is True and m["version"] == "1.2.3"
    # wrong token hash -> not found
    assert (
        own.find_challenge_match("npm", data, own.hash_token("other"))["found"] is False
    )


def test_find_challenge_match_pypi():
    t = own.new_challenge_token()
    h = own.hash_token(t)
    data = {
        "info": {"version": "2.0.0", "keywords": f"mcp, {own.challenge_keyword(t)}"}
    }
    m = own.find_challenge_match("pypi", data, h)
    assert m["found"] is True and m["version"] == "2.0.0"


def test_find_challenge_match_absent():
    data = {
        "dist-tags": {"latest": "1.0.0"},
        "versions": {"1.0.0": {"keywords": ["mcp"]}},
    }
    assert own.find_challenge_match("npm", data, own.hash_token("x"))["found"] is False


# --- endpoint flow -----------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_then_verify_npm(client, monkeypatch):
    token, _ = await setup_publisher_user(
        client, email="chal@agentnode.dev", username="chaluser", pub_slug="chal-pub"
    )

    issue = await client.post(
        "/v1/mcp/ownership/challenge",
        json={"registry": "npm", "package_name": "my-mcp"},
        headers=_auth(token),
    )
    assert issue.status_code == 200, issue.text
    data = issue.json()
    challenge_token = data["token"]
    keyword = data["keyword"]
    assert keyword.endswith(challenge_token)

    # Before the keyword is published -> pending, not verified.
    async def fake_npm_no_kw(name):
        return {
            "dist-tags": {"latest": "1.0.0"},
            "versions": {"1.0.0": {"keywords": ["mcp"]}},
        }

    monkeypatch.setattr("app.mcp.registry_verify._fetch_npm", fake_npm_no_kw)
    v1 = await client.post(
        "/v1/mcp/ownership/challenge/verify",
        json={"registry": "npm", "package_name": "my-mcp"},
        headers=_auth(token),
    )
    assert v1.status_code == 200
    assert v1.json()["verified"] is False
    assert v1.json()["status"] == "pending"

    # Publish a version carrying the keyword -> verified (strong).
    async def fake_npm_with_kw(name):
        return {
            "dist-tags": {"latest": "1.1.0"},
            "versions": {"1.1.0": {"keywords": ["mcp", keyword]}},
        }

    monkeypatch.setattr("app.mcp.registry_verify._fetch_npm", fake_npm_with_kw)
    v2 = await client.post(
        "/v1/mcp/ownership/challenge/verify",
        json={"registry": "npm", "package_name": "my-mcp"},
        headers=_auth(token),
    )
    assert v2.status_code == 200, v2.text
    assert v2.json()["verified"] is True
    assert v2.json()["version"] == "1.1.0"


@pytest.mark.asyncio
async def test_verify_without_challenge_404(client):
    token, _ = await setup_publisher_user(
        client, email="noc@agentnode.dev", username="nocuser", pub_slug="noc-pub"
    )
    r = await client.post(
        "/v1/mcp/ownership/challenge/verify",
        json={"registry": "npm", "package_name": "never-issued"},
        headers=_auth(token),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_challenge_requires_publisher(client):
    from tests.conftest import register_and_login

    token = await register_and_login(
        client, email="plainuser@agentnode.dev", username="plainuser"
    )
    r = await client.post(
        "/v1/mcp/ownership/challenge",
        json={"registry": "npm", "package_name": "x"},
        headers=_auth(token),
    )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_invalid_registry_rejected(client):
    token, _ = await setup_publisher_user(
        client, email="reg@agentnode.dev", username="reguser", pub_slug="reg-pub"
    )
    r = await client.post(
        "/v1/mcp/ownership/challenge",
        json={"registry": "cargo", "package_name": "x"},
        headers=_auth(token),
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_verified_challenge_makes_ownership_strong_but_not_auto_publish(
    client, monkeypatch
):
    """End-to-end: a verified publish-challenge yields STRONG ownership evidence,
    so the ownership gate passes — but auto_publish_eligible stays False because
    sandbox_smoke is still a future blocker."""
    from app.mcp.gates import evaluate_gates

    # Simulate the state a verified challenge produces: a strong, verified claim.
    ev = own.derive_ownership_evidence("publish_challenge", "verified")
    assert ev["auto_eligible"] is True

    r = evaluate_gates(
        manifest={
            "runtime": "mcp",
            "package_id": "p",
            "mcp_server": {"npm_package": "p"},
        },
        server_verification={
            "server_status": "verified",
            "package_exists": True,
            "resolved_version": "1.0.0",
            "repo_consistency": "match",
            "command_pinning": "pinned",
            "errors": [],
        },
        report={"status": "TESTED", "actions": []},
        typosquat_hit=False,
        ownership=ev,
    )
    own_gate = next(
        g for g in r["gates"] if g["id"] == "ownership_automatically_proven"
    )
    assert own_gate["passed"] is True
    assert r["auto_publish_eligible"] is False
    assert "sandbox_smoke" in r["future_blockers"]
