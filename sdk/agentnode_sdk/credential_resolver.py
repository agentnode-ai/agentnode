"""Credential resolution — resolve secrets for connector packages.

Resolution chain (configurable via credentials.resolve_mode in config):
- "auto" (default): env → local file → API
- "env": environment variables only
- "local": local file only (~/.agentnode/credentials.json)
- "api": server-side only

For api_key auth:   AGENTNODE_CRED_SLACK → api_key
For oauth2 auth:    AGENTNODE_CRED_SLACK → access_token

API mode returns a proxy handle that routes requests through the backend
(secret never leaves the server).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from agentnode_sdk.credential_handle import CredentialHandle

logger = logging.getLogger("agentnode.credential_resolver")

_ENV_PREFIX = "AGENTNODE_CRED_"


def _load_resolve_mode() -> str:
    """Read credentials.resolve_mode from config. Default: 'auto'."""
    try:
        from agentnode_sdk.config import config_dir
        config_path = config_dir() / "config.json"
        if config_path.exists():
            with open(config_path) as f:
                cfg = json.load(f)
            return cfg.get("credentials", {}).get("resolve_mode", "auto")
    except Exception:
        pass
    return "auto"


def resolve_handle(
    provider: str,
    auth_type: str,
    *,
    scopes: list[str] | None = None,
    allowed_domains: list[str] | None = None,
) -> CredentialHandle | None:
    """Resolve a CredentialHandle using the configured resolution chain.

    Returns None if no credential is found (caller decides whether to fail).
    """
    provider = provider.lower()
    mode = _load_resolve_mode()

    if mode == "env":
        handle = _resolve_from_env(provider, auth_type, scopes=scopes, allowed_domains=allowed_domains)
    elif mode == "local":
        handle = _resolve_from_local_file(provider, auth_type, scopes=scopes, allowed_domains=allowed_domains)
    elif mode == "api":
        handle = _resolve_from_api(provider, auth_type, scopes=scopes, allowed_domains=allowed_domains)
    else:
        # auto: try env first, then local file, then API
        handle = _resolve_from_env(provider, auth_type, scopes=scopes, allowed_domains=allowed_domains)
        if handle is None:
            handle = _resolve_from_local_file(provider, auth_type, scopes=scopes, allowed_domains=allowed_domains)
        if handle is None:
            handle = _resolve_from_api(provider, auth_type, scopes=scopes, allowed_domains=allowed_domains)

    if handle is not None:
        logger.debug(
            "Credential resolved for provider=%s via source=%s (mode=%s)",
            provider, handle.source, mode,
        )
    else:
        logger.debug(
            "No credential found for provider=%s (mode=%s)", provider, mode,
        )

    return handle


def _resolve_from_env(
    provider: str,
    auth_type: str,
    *,
    scopes: list[str] | None = None,
    allowed_domains: list[str] | None = None,
) -> CredentialHandle | None:
    """Resolve a CredentialHandle from environment variables.

    Looks for AGENTNODE_CRED_{PROVIDER_UPPER}. Returns None if
    no credential is found.
    """
    env_key = f"{_ENV_PREFIX}{provider.upper().replace('-', '_')}"
    secret = os.environ.get(env_key)

    if not secret:
        logger.debug(
            "No credential found for provider=%s (checked %s)",
            provider, env_key,
        )
        return None

    # Build secret_data based on auth_type
    if auth_type == "oauth2":
        secret_data = {"access_token": secret}
    else:
        # Default: api_key
        secret_data = {"api_key": secret}

    return CredentialHandle(
        provider=provider,
        auth_type=auth_type,
        scopes=scopes or [],
        allowed_domains=allowed_domains or [],
        secret_data=secret_data,
        source="env",
    )


def _resolve_from_local_file(
    provider: str,
    auth_type: str,
    *,
    scopes: list[str] | None = None,
    allowed_domains: list[str] | None = None,
) -> CredentialHandle | None:
    """Resolve a CredentialHandle from ~/.agentnode/credentials.json.

    Reads tokens stored via `agentnode auth <provider>`.
    Returns None if no credential is found for the provider.
    """
    try:
        from agentnode_sdk.credential_store import get_credential
    except Exception:
        logger.debug("credential_store not available, skipping local file resolution")
        return None

    entry = get_credential(provider)
    if entry is None:
        logger.debug("No local credential for provider=%s", provider)
        return None

    token = entry.get("access_token", "")
    if not token:
        logger.debug("Local credential for provider=%s has empty token", provider)
        return None

    stored_auth_type = entry.get("auth_type", auth_type)
    stored_scopes = entry.get("scopes", [])

    if stored_auth_type == "oauth2":
        secret_data = {"access_token": token}
    else:
        secret_data = {"api_key": token}

    return CredentialHandle(
        provider=provider,
        auth_type=stored_auth_type,
        scopes=scopes or stored_scopes,
        allowed_domains=allowed_domains or [],
        secret_data=secret_data,
        source="local_file",
    )


def _resolve_from_api(
    provider: str,
    auth_type: str,
    *,
    scopes: list[str] | None = None,
    allowed_domains: list[str] | None = None,
) -> CredentialHandle | None:
    """Resolve a credential via the backend API proxy.

    The secret never leaves the server. Instead, the SDK receives a
    short-lived resolve_token that can be used with the /proxy endpoint.

    Returns a ProxyCredentialHandle that routes authorized_request()
    calls through the backend.
    """
    api_base = os.environ.get("AGENTNODE_API_URL", "").rstrip("/")
    session_token = os.environ.get("AGENTNODE_SESSION_TOKEN", "")

    if not api_base or not session_token:
        logger.debug("API credential resolution skipped: no API URL or session token")
        return None

    try:
        import httpx

        resp = httpx.get(
            f"{api_base}/v1/credentials/resolve/{provider}",
            headers={"Authorization": f"Bearer {session_token}"},
            timeout=10.0,
        )

        if resp.status_code == 404:
            logger.debug("No credential found for provider=%s via API", provider)
            return None
        if resp.status_code == 401:
            logger.debug("API auth failed for credential resolution")
            return None
        if resp.status_code != 200:
            logger.warning("Credential resolve API returned %d", resp.status_code)
            return None

        data = resp.json()
        resolve_token = data.get("resolve_token")
        if not resolve_token:
            logger.warning("No resolve_token in API response")
            return None

        return ProxyCredentialHandle(
            provider=provider,
            auth_type=auth_type,
            scopes=scopes or [],
            allowed_domains=allowed_domains or data.get("allowed_domains", []),
            resolve_token=resolve_token,
            api_base=api_base,
            session_token=session_token,
        )

    except Exception as exc:
        logger.debug("API credential resolution failed: %s", exc)
        return None


class ProxyCredentialHandle(CredentialHandle):
    """Credential handle that proxies requests through the backend.

    The secret stays on the server. authorized_request() calls go through
    POST /v1/credentials/proxy with a short-lived resolve_token.
    """

    def __init__(
        self,
        *,
        provider: str,
        auth_type: str,
        scopes: list[str],
        allowed_domains: list[str],
        resolve_token: str,
        api_base: str,
        session_token: str,
    ) -> None:
        # Initialize parent with empty secret_data (secret is on server)
        super().__init__(
            provider=provider,
            auth_type=auth_type,
            scopes=scopes,
            allowed_domains=allowed_domains,
            secret_data={},  # No local secret
            source="server",
        )
        self._resolve_token = resolve_token
        self._api_base = api_base
        self._session_token = session_token

    def authorized_request(
        self,
        method: str,
        url: str,
        *,
        json: dict | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        """Route the request through the backend proxy endpoint."""
        import httpx
        from agentnode_sdk.credential_handle import AuthorizedResponse

        if not self.is_domain_allowed(url):
            from urllib.parse import urlparse
            host = urlparse(url).hostname or "unknown"
            raise PermissionError(
                f"ProxyCredentialHandle for '{self._provider}' cannot access "
                f"host '{host}'. Allowed domains: {self._allowed_domains}"
            )

        proxy_body: dict[str, Any] = {
            "resolve_token": self._resolve_token,
            "method": method,
            "url": url,
        }
        if json is not None:
            proxy_body["json"] = json

        resp = httpx.post(
            f"{self._api_base}/v1/credentials/proxy",
            headers={
                "Authorization": f"Bearer {self._session_token}",
                "Content-Type": "application/json",
            },
            json=proxy_body,
            timeout=timeout,
        )

        if resp.status_code == 401:
            raise PermissionError("Resolve token expired or invalid")

        result = resp.json()
        return AuthorizedResponse(
            status_code=result.get("status_code", resp.status_code),
            headers={},
            body=result.get("body", ""),
        )

    def _build_auth_headers(self) -> dict[str, str]:
        """Proxy handles don't build local auth headers."""
        return {}

    def __getstate__(self):
        raise TypeError("ProxyCredentialHandle is not serializable.")

    def __reduce__(self):
        raise TypeError("ProxyCredentialHandle cannot be pickled.")
