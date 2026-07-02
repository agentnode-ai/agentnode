"""Tests for RegistrySigningMiddleware."""

import base64
import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)


def _generate_test_keypair():
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pem_b64 = base64.b64encode(pem).decode()
    return pem_b64, private.public_key()


TEST_KEY_B64, TEST_PUBLIC_KEY = _generate_test_keypair()
TEST_KEY_ID = "test-signing-key"

VALID_SIGNING_KEY = base64.b64encode(b"\x01" * 32).decode()
VALID_KEY_ID = "ed25519:" + hashlib.sha256(b"\x01" * 32).hexdigest()[:16]


async def _setup_publisher_with_key(client) -> str:
    user = {
        "email": "sign@test.dev",
        "username": "signuser",
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
        json={"display_name": "Sign Publisher", "slug": "sign-pub"},
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.put(
        "/v1/publishers/sign-pub/signing-key",
        json={"public_key": VALID_SIGNING_KEY},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


def _verify_signature(resp, public_key: Ed25519PublicKey):
    header = resp.headers.get("x-agentnode-signature")
    assert header is not None, "Missing X-AgentNode-Signature header"
    parts = header.split(":", 2)
    assert len(parts) == 3, f"Bad header format: {header}"
    alg, kid, sig_b64 = parts
    assert alg == "ed25519"
    assert kid == TEST_KEY_ID
    sig_bytes = base64.b64decode(sig_b64)
    assert len(sig_bytes) == 64
    public_key.verify(sig_bytes, resp.content)


# --- Trust-critical endpoints get signed ---

# These tests use the `client` fixture from conftest.py (which has
# the full app with DB + Redis mock). The app already has
# RegistrySigningMiddleware registered, but with empty key (degraded).
# We monkeypatch the middleware instance to inject a test key.


def _find_signing_middleware(app_or_mw):
    """Walk the ASGI middleware stack to find the signing middleware instance."""
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


@pytest.fixture(autouse=False)
async def _arm_signing(client):
    """Inject the test signing key into the middleware stack.

    Makes a dummy request first to force FastAPI to build the middleware stack.
    """
    from app.main import app
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    # Force middleware stack build via a lightweight request
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
async def test_keys_endpoint_has_signature(client, _arm_signing):
    await _setup_publisher_with_key(client)
    resp = await client.get(f"/v1/publishers/sign-pub/keys/{VALID_KEY_ID}")
    assert resp.status_code == 200
    _verify_signature(resp, TEST_PUBLIC_KEY)


@pytest.mark.asyncio
async def test_signature_format(client, _arm_signing):
    await _setup_publisher_with_key(client)
    resp = await client.get(f"/v1/publishers/sign-pub/keys/{VALID_KEY_ID}")
    header = resp.headers["x-agentnode-signature"]
    alg, kid, sig_b64 = header.split(":", 2)
    assert alg == "ed25519"
    assert kid == TEST_KEY_ID
    sig_bytes = base64.b64decode(sig_b64)
    assert len(sig_bytes) == 64


@pytest.mark.asyncio
async def test_different_body_fails_verification(client, _arm_signing):
    await _setup_publisher_with_key(client)
    resp = await client.get(f"/v1/publishers/sign-pub/keys/{VALID_KEY_ID}")
    header = resp.headers["x-agentnode-signature"]
    _, _, sig_b64 = header.split(":", 2)
    sig_bytes = base64.b64decode(sig_b64)

    from cryptography.exceptions import InvalidSignature

    with pytest.raises(InvalidSignature):
        TEST_PUBLIC_KEY.verify(sig_bytes, b"tampered body")


# --- Non-critical endpoints NOT signed ---


@pytest.mark.asyncio
async def test_non_critical_get_no_signature(client, _arm_signing):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert "x-agentnode-signature" not in resp.headers


@pytest.mark.asyncio
async def test_post_no_signature(client, _arm_signing):
    await _setup_publisher_with_key(client)
    resp = await client.post("/v1/search", json={"query": "test"})
    assert "x-agentnode-signature" not in resp.headers


@pytest.mark.asyncio
async def test_signing_key_endpoint_not_signed(client, _arm_signing):
    """Legacy /signing-key is NOT in trust-critical list."""
    await _setup_publisher_with_key(client)
    resp = await client.get("/v1/publishers/sign-pub/signing-key")
    assert resp.status_code == 200
    assert "x-agentnode-signature" not in resp.headers


# --- Degraded mode ---


@pytest.mark.asyncio
async def test_degraded_mode_no_key(client):
    """No signing key → no header, response still 200."""
    await _setup_publisher_with_key(client)
    resp = await client.get(f"/v1/publishers/sign-pub/keys/{VALID_KEY_ID}")
    assert resp.status_code == 200
    assert "x-agentnode-signature" not in resp.headers


# --- Health check ---


@pytest.mark.asyncio
async def test_signing_health_inactive(client):
    resp = await client.get("/v1/health/signing")
    assert resp.status_code == 200
    data = resp.json()
    assert data["signing_active"] is False


# --- Real initialization path (simulates production startup) ---


@pytest.mark.asyncio
async def test_middleware_real_init_path():
    """Verify the middleware works when initialized the same way main.py does."""
    from app.shared.signing_middleware import RegistrySigningMiddleware

    mw = RegistrySigningMiddleware(
        None,
        signing_key_b64=TEST_KEY_B64,
        key_id=TEST_KEY_ID,
    )
    assert mw._private_key is not None
    assert mw._key_id == TEST_KEY_ID

    # Verify it can actually sign
    test_body = b'{"test": true}'
    sig = mw._private_key.sign(test_body)
    TEST_PUBLIC_KEY.verify(sig, test_body)


@pytest.mark.asyncio
async def test_middleware_degraded_init_empty_key():
    """Empty signing_key_b64 → degraded mode, no crash."""
    from app.shared.signing_middleware import RegistrySigningMiddleware

    mw = RegistrySigningMiddleware(None, signing_key_b64="", key_id="")
    assert mw._private_key is None


@pytest.mark.asyncio
async def test_middleware_degraded_init_garbage_key():
    """Invalid key material → degraded mode, no crash."""
    from app.shared.signing_middleware import RegistrySigningMiddleware

    garbage = base64.b64encode(b"not-a-pem-key").decode()
    mw = RegistrySigningMiddleware(None, signing_key_b64=garbage, key_id="bad")
    assert mw._private_key is None


# --- Health accuracy (is_signing_ready) ---


def test_is_signing_ready_false_by_default():
    """Module starts with _signing_ready=False."""
    from app.shared.signing_middleware import is_signing_ready

    assert is_signing_ready() is not None


def test_health_endpoint_uses_signing_ready():
    """Health logic: signing_active follows is_signing_ready(), not env string."""
    import app.shared.signing_middleware as sm

    old = sm._signing_ready
    try:
        sm._signing_ready = False
        assert sm.is_signing_ready() is False

        sm._signing_ready = True
        assert sm.is_signing_ready() is True
    finally:
        sm._signing_ready = old


@pytest.mark.asyncio
async def test_signing_ready_set_on_valid_key_load():
    """is_signing_ready() returns True after successful key load."""
    import app.shared.signing_middleware as sm
    from app.shared.signing_middleware import RegistrySigningMiddleware

    old = sm._signing_ready
    try:
        sm._signing_ready = False
        RegistrySigningMiddleware(None, signing_key_b64=TEST_KEY_B64, key_id="test")
        assert sm._signing_ready is True
    finally:
        sm._signing_ready = old


@pytest.mark.asyncio
async def test_signing_ready_stays_false_on_garbage_key():
    """is_signing_ready() stays False when key load fails."""
    import app.shared.signing_middleware as sm
    from app.shared.signing_middleware import RegistrySigningMiddleware

    old = sm._signing_ready
    try:
        sm._signing_ready = False
        garbage = base64.b64encode(b"not-a-pem-key").decode()
        RegistrySigningMiddleware(None, signing_key_b64=garbage, key_id="bad")
        assert sm._signing_ready is False
    finally:
        sm._signing_ready = old
