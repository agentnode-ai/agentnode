"""How the CLI presents an AgentNode credential to the backend.

A dashboard API key (``ank_...``) is read from ``X-API-Key``; ``Authorization: Bearer``
is decoded as a login JWT, so an API key sent as Bearer is always refused (401).
Anything that is not an API key is passed on as Bearer, which is what a JWT needs.
"""

from __future__ import annotations

API_KEY_PREFIX = "ank_"


def auth_headers(credential: str) -> dict[str, str]:
    credential = (credential or "").strip()
    if credential.startswith(API_KEY_PREFIX):
        return {"X-API-Key": credential}
    return {"Authorization": f"Bearer {credential}"}
