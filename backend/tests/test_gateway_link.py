"""Task #8: the website hands a signed-in user's identity to a gateway as a signed link grant.

POST /v1/auth/gateway-link {"gateway_id": "<32 hex>"} -> {"grant", "expires_at", "key_id"} for the
signed-in user, signed with the website's link key. The gateway checks it
(sdk/agentnode_sdk/gateway/website_link.py). Both sides are held to one contract
(shared/website-link-grant-contract.json): from the same inputs they produce the same grant.
Local fixtures only; the contract key is a published TEST-ONLY key.
"""

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.auth import gateway_link as gl
from app.config import settings

CONTRACT = (
    Path(__file__).resolve().parents[2] / "shared" / "website-link-grant-contract.json"
)
GATEWAY = "ab" * 16


def _b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _contract():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_the_website_produces_the_contract_grant_byte_for_byte():
    doc = _contract()
    grant = gl.issue_grant(
        _b64d(doc["seed_b64url"]),
        key_id=doc["key_id"],
        gateway_id=doc["gateway_id"],
        website_user=doc["website_user"],
        now=doc["now"],
        lifetime=doc["lifetime"],
        nonce=doc["nonce"],
    )
    assert grant == doc["grant"]


@pytest.fixture
def link_key(monkeypatch):
    seed = bytes(range(40, 72))
    monkeypatch.setattr(
        settings,
        "GATEWAY_LINK_SIGNING_KEY",
        base64.urlsafe_b64encode(seed).decode().rstrip("="),
        raising=False,
    )
    monkeypatch.setattr(settings, "GATEWAY_LINK_KEY_ID", "web-test", raising=False)
    return Ed25519PrivateKey.from_private_bytes(seed).public_key()


async def _signed_in(client):
    user = {
        "email": "link-user@agentnode.dev",
        "username": "linkuser",
        "password": "Str0ng!Passw0rd-x",
    }
    r = await client.post("/v1/auth/register", json=user)
    assert r.status_code in (200, 201), r.text
    r = await client.post(
        "/v1/auth/login", json={"email": user["email"], "password": user["password"]}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.mark.asyncio
async def test_a_signed_in_user_gets_a_grant_the_gateway_key_verifies(client, link_key):
    headers = await _signed_in(client)
    r = await client.post(
        "/v1/auth/gateway-link", json={"gateway_id": GATEWAY}, headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key_id"] == "web-test"
    payload, sig = body["grant"].split(".")
    key_id, signature = _b64d(sig).split(b":", 1)
    link_key.verify(signature, ("%s|%s" % (key_id.decode(), payload)).encode())
    claims = json.loads(_b64d(payload))
    assert claims["aud"] == GATEWAY and claims["iss"] == "agentnode-website"
    assert claims["sub"].startswith("u-") and len(claims["sub"]) == 34
    assert 0 < claims["exp"] - claims["iat"] <= 300
    assert set(claims) == {"v", "iss", "aud", "sub", "iat", "exp", "nonce"}


@pytest.mark.asyncio
async def test_two_requests_give_two_single_use_grants_for_the_same_user(
    client, link_key
):
    headers = await _signed_in(client)
    a = (
        await client.post(
            "/v1/auth/gateway-link", json={"gateway_id": GATEWAY}, headers=headers
        )
    ).json()
    b = (
        await client.post(
            "/v1/auth/gateway-link", json={"gateway_id": GATEWAY}, headers=headers
        )
    ).json()
    ca, cb = (json.loads(_b64d(x["grant"].split(".")[0])) for x in (a, b))
    assert ca["sub"] == cb["sub"] and ca["nonce"] != cb["nonce"]


@pytest.mark.asyncio
async def test_without_sign_in_there_is_no_grant(client, link_key):
    r = await client.post("/v1/auth/gateway-link", json={"gateway_id": GATEWAY})
    assert r.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "xyz", "AB" * 16, "ab" * 17, "../../etc"])
async def test_a_malformed_gateway_id_is_refused(client, link_key, bad):
    headers = await _signed_in(client)
    r = await client.post(
        "/v1/auth/gateway-link", json={"gateway_id": bad}, headers=headers
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_without_a_configured_key_the_website_says_so(client, monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_LINK_SIGNING_KEY", "", raising=False)
    headers = await _signed_in(client)
    r = await client.post(
        "/v1/auth/gateway-link", json={"gateway_id": GATEWAY}, headers=headers
    )
    assert r.status_code == 503
    assert "grant" not in r.text
