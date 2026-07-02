"""SDK cross-verification test — proves the body-byte invariant.

This is the single most important test in the signing suite.
It proves: bytes_signed_by_backend == bytes_verified_by_sdk.

The test sends a request through the real backend (with middleware
signing), extracts the response body and signature header, then
feeds them to the SDK's verify_registry_response() function.
If the SDK accepts the signature, the body-byte invariant holds.
"""

import base64
import hashlib
import sys
from pathlib import Path
from types import MappingProxyType

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

# Add SDK to sys.path so we can import agentnode_sdk
SDK_ROOT = Path(__file__).resolve().parent.parent.parent / "sdk"
if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))


def _generate_test_keypair():
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pem_b64 = base64.b64encode(pem).decode()
    pub_b64 = base64.b64encode(private.public_key().public_bytes_raw()).decode()
    return pem_b64, pub_b64, private.public_key()


TEST_KEY_B64, TEST_PUB_B64, TEST_PUBLIC_KEY = _generate_test_keypair()
TEST_KEY_ID = "test-signing-key"

PUBLISHER_KEY = base64.b64encode(b"\x01" * 32).decode()
PUBLISHER_KEY_ID = "ed25519:" + hashlib.sha256(b"\x01" * 32).hexdigest()[:16]


async def _setup_publisher(client) -> str:
    user = {
        "email": "xverify@test.dev",
        "username": "xverify",
        "password": "TestPass123!",
    }
    await client.post("/v1/auth/register", json=user)
    login = await client.post(
        "/v1/auth/login",
        json={
            "email": user["email"],
            "password": user["password"],
        },
    )
    token = login.json()["access_token"]
    await client.post(
        "/v1/publishers",
        json={"display_name": "XVerify Publisher", "slug": "xverify-pub"},
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.put(
        "/v1/publishers/xverify-pub/signing-key",
        json={"public_key": PUBLISHER_KEY},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


def _find_signing_middleware(app_or_mw):
    from app.shared.signing_middleware import RegistrySigningMiddleware

    mw = app_or_mw
    visited = set()
    while mw is not None:
        obj_id = id(mw)
        if obj_id in visited:
            break
        visited.add(obj_id)
        if isinstance(mw, RegistrySigningMiddleware):
            return mw
        mw = getattr(mw, "app", None)
    return None


@pytest.fixture
async def _arm_signing(client):
    from app.main import app
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    await client.get("/health")
    mw = _find_signing_middleware(app.middleware_stack)
    if mw is None:
        pytest.skip("Could not find RegistrySigningMiddleware in stack")
    pem_bytes = base64.b64decode(TEST_KEY_B64)
    mw._private_key = load_pem_private_key(pem_bytes, password=None)
    mw._key_id = TEST_KEY_ID
    yield
    mw._private_key = None
    mw._key_id = ""


@pytest.mark.asyncio
async def test_sdk_verifies_backend_signature(client, _arm_signing, monkeypatch):
    """The SDK's verify_registry_response() accepts a real backend-signed response.

    This proves the body-byte invariant:
      signed_bytes (backend middleware) == response_body (SDK verification input)
    """
    await _setup_publisher(client)

    resp = await client.get(f"/v1/publishers/xverify-pub/keys/{PUBLISHER_KEY_ID}")
    assert resp.status_code == 200

    sig_header = resp.headers.get("x-agentnode-signature")
    assert sig_header is not None, "Backend did not sign the response"

    from agentnode_sdk.registry_trust import (
        RegistryKey,
        RegistrySignatureStatus,
        verify_registry_response,
    )
    import agentnode_sdk.registry_trust as rt_module

    test_registry_keys = MappingProxyType(
        {
            TEST_KEY_ID: RegistryKey(
                key_id=TEST_KEY_ID,
                algorithm="ed25519",
                public_key=TEST_PUB_B64,
                not_after="2099-12-31",
            ),
        }
    )
    monkeypatch.setattr(rt_module, "REGISTRY_KEYS", test_registry_keys)

    result = verify_registry_response(
        response_body=resp.content,
        signature_header=sig_header,
    )
    assert result.status == RegistrySignatureStatus.VALID, (
        f"SDK rejected backend signature: {result.status.value} — {result.error}"
    )
    assert result.key_id == TEST_KEY_ID


@pytest.mark.asyncio
async def test_sdk_rejects_tampered_body(client, _arm_signing, monkeypatch):
    """Tampered body fails SDK verification — proves integrity check works."""
    await _setup_publisher(client)

    resp = await client.get(f"/v1/publishers/xverify-pub/keys/{PUBLISHER_KEY_ID}")
    assert resp.status_code == 200

    sig_header = resp.headers.get("x-agentnode-signature")
    assert sig_header is not None

    from agentnode_sdk.registry_trust import (
        RegistryKey,
        RegistrySignatureStatus,
        verify_registry_response,
    )
    import agentnode_sdk.registry_trust as rt_module

    test_registry_keys = MappingProxyType(
        {
            TEST_KEY_ID: RegistryKey(
                key_id=TEST_KEY_ID,
                algorithm="ed25519",
                public_key=TEST_PUB_B64,
                not_after="2099-12-31",
            ),
        }
    )
    monkeypatch.setattr(rt_module, "REGISTRY_KEYS", test_registry_keys)

    tampered = resp.content + b"x"
    result = verify_registry_response(
        response_body=tampered,
        signature_header=sig_header,
    )
    assert result.status == RegistrySignatureStatus.INVALID


@pytest.mark.asyncio
async def test_sdk_rejects_missing_header_enforcement(
    client, _arm_signing, monkeypatch
):
    """With REGISTRY_KEYS populated, missing header → MISSING (downgrade)."""
    from agentnode_sdk.registry_trust import (
        RegistryKey,
        RegistrySignatureStatus,
        verify_registry_response,
    )
    import agentnode_sdk.registry_trust as rt_module

    test_registry_keys = MappingProxyType(
        {
            TEST_KEY_ID: RegistryKey(
                key_id=TEST_KEY_ID,
                algorithm="ed25519",
                public_key=TEST_PUB_B64,
                not_after="2099-12-31",
            ),
        }
    )
    monkeypatch.setattr(rt_module, "REGISTRY_KEYS", test_registry_keys)

    result = verify_registry_response(
        response_body=b'{"test": true}',
        signature_header=None,
    )
    assert result.status == RegistrySignatureStatus.MISSING
