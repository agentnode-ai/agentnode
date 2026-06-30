"""Registry response signing middleware (pure ASGI).

Signs trust-critical GET responses with Ed25519. Adds
X-AgentNode-Signature header: ed25519:{key_id}:{base64_sig}.

Degraded mode: if no signing key is configured or key is invalid,
responses are served without the signature header. Never returns 500.
"""

import base64
import logging
import re

logger = logging.getLogger("agentnode.signing")

_signing_ready: bool = False


def is_signing_ready() -> bool:
    """Whether the signing middleware successfully loaded a private key.

    Set once at middleware init. Not runtime-mutable — reflects startup state only.
    """
    return _signing_ready


_TRUST_CRITICAL_PATTERNS = (
    re.compile(r"^/v1/packages/[^/]+$"),
    re.compile(r"^/v1/packages/[^/]+/install-info$"),
    re.compile(r"^/v1/publishers/[^/]+/keys/[^/]+$"),
)


def _is_trust_critical(path: str) -> bool:
    normalized = path.rstrip("/")
    return any(p.match(normalized) for p in _TRUST_CRITICAL_PATTERNS)


class RegistrySigningMiddleware:
    """Pure ASGI middleware that signs trust-critical response bodies.

    Collects response body chunks for trust-critical GET requests,
    signs the concatenated bytes, and injects the signature header
    into the response start message.
    """

    def __init__(self, app, *, signing_key_b64: str = "", key_id: str = ""):
        self.app = app
        self._private_key = None
        self._key_id = key_id
        self._degraded_logged = False

        if signing_key_b64:
            try:
                from cryptography.hazmat.primitives.serialization import (
                    load_pem_private_key,
                )

                pem_bytes = base64.b64decode(signing_key_b64)
                self._private_key = load_pem_private_key(pem_bytes, password=None)
                global _signing_ready
                _signing_ready = True
                logger.info("Registry signing key loaded (key_id=%s)", key_id)
            except Exception:
                logger.critical(
                    "Failed to load REGISTRY_SIGNING_KEY — responses will not be signed"
                )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")

        if method != "GET" or not _is_trust_critical(path):
            await self.app(scope, receive, send)
            return

        if self._private_key is None:
            if not self._degraded_logged:
                logger.critical(
                    "No signing key configured — trust-critical responses served unsigned"
                )
                self._degraded_logged = True
            await self.app(scope, receive, send)
            return

        body_chunks: list[bytes] = []
        start_message = None

        async def capture_send(message):
            nonlocal start_message
            if message["type"] == "http.response.start":
                start_message = message
            elif message["type"] == "http.response.body":
                body_chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    body = b"".join(body_chunks)
                    sig_bytes = self._private_key.sign(body)
                    sig_b64 = base64.b64encode(sig_bytes).decode()
                    header_value = f"ed25519:{self._key_id}:{sig_b64}"

                    headers = list(start_message.get("headers", []))
                    headers.append((b"x-agentnode-signature", header_value.encode()))
                    await send({**start_message, "headers": headers})
                    await send(message)
                else:
                    pass
            else:
                await send(message)

        await self.app(scope, receive, capture_send)
