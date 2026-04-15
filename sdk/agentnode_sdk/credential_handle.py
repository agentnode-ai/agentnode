"""CredentialHandle — provider-bound, domain-restricted credential abstraction.

Security invariants (S3, S8, S12):
- Handle is bound to a specific provider (e.g. "slack", "github")
- Allowed hosts are fixed at construction from the connector profile
- No .token, .headers, or .secret property — secrets stay internal
- Not serializable — __getstate__ raises, __repr__ never shows secrets
- Secrets never appear in exceptions, logs, or tool results
- Cannot be used as a general-purpose HTTP proxy

Preferred interface: authorized_request() — token never leaves the handle.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("agentnode.credential_handle")


@dataclass
class AuthorizedResponse:
    """Response from an authorized_request() call. Contains no secrets."""

    status_code: int
    headers: dict[str, str]
    body: str


class CredentialHandle:
    """Opaque handle to an encrypted credential.

    Tool code receives this handle and calls `authorized_request()` to make
    authenticated API calls. The handle validates that the target host is
    in the allowed_domains list before attaching credentials.

    This class is intentionally NOT a general HTTP client. It is a narrow
    gateway that enforces provider-bound, domain-restricted access.
    """

    __slots__ = (
        "_provider",
        "_auth_type",
        "_scopes",
        "_allowed_domains",
        "_secret_data",
        "_source",
    )

    def __init__(
        self,
        *,
        provider: str,
        auth_type: str,
        scopes: list[str],
        allowed_domains: list[str],
        secret_data: dict,
        source: str = "",
    ) -> None:
        self._provider = provider
        self._auth_type = auth_type
        self._scopes = list(scopes)
        self._allowed_domains = [d.lower() for d in allowed_domains]
        # Secret data is stored internally — never exposed via properties
        self._secret_data = dict(secret_data)
        self._source = source

    # --- Public metadata (safe to expose) ---

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def auth_type(self) -> str:
        return self._auth_type

    @property
    def scopes(self) -> list[str]:
        return list(self._scopes)

    @property
    def allowed_domains(self) -> list[str]:
        return list(self._allowed_domains)

    @property
    def source(self) -> str:
        """How this credential was resolved: 'env', 'local_file', 'server', or ''."""
        return self._source

    # --- Security: no secret access ---

    # S12: No .token, .headers, or .secret property.
    # Tool code MUST use authorized_request() to interact with the credential.

    # --- Domain validation (S3, S8) ---

    def is_domain_allowed(self, url: str) -> bool:
        """Check if a URL's host is in the allowed domains list.

        Returns True if:
        - allowed_domains is empty (no restriction — fallback for early connector packs)
        - The URL's hostname matches one of the allowed domains
        """
        if not self._allowed_domains:
            return True

        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
        except Exception:
            return False

        return host in self._allowed_domains

    def _build_auth_headers(self) -> dict[str, str]:
        """Build authentication headers from secret data.

        Internal only — never called directly by tool code.
        """
        if self._auth_type == "api_key":
            key = self._secret_data.get("api_key", "")
            header_name = self._secret_data.get("header_name", "Authorization")
            header_prefix = self._secret_data.get("header_prefix", "Bearer")
            if header_prefix:
                return {header_name: f"{header_prefix} {key}"}
            return {header_name: key}

        if self._auth_type == "oauth2":
            token = self._secret_data.get("access_token", "")
            return {"Authorization": f"Bearer {token}"}

        return {}

    def authorized_request_headers(self, url: str) -> dict[str, str]:
        """Get auth headers for a request, after domain validation.

        Prefer ``authorized_request()`` which keeps the token inside the
        handle. This method is an escape hatch for callers that need raw
        headers (e.g. WebSocket upgrades, streaming protocols).

        Raises:
            PermissionError: If the target domain is not allowed.
        """
        if not self.is_domain_allowed(url):
            try:
                host = urlparse(url).hostname or "unknown"
            except Exception:
                host = "unknown"
            raise PermissionError(
                f"CredentialHandle for '{self._provider}' cannot access "
                f"host '{host}'. Allowed domains: {self._allowed_domains}"
            )
        return self._build_auth_headers()

    # --- Authorized HTTP request (preferred interface) ---

    def authorized_request(
        self,
        method: str,
        url: str,
        *,
        json: dict | None = None,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> "AuthorizedResponse":
        """Make an authenticated HTTP request through the handle.

        This is the PREFERRED way to use the credential. The token never
        leaves the handle — it is injected into the request internally.
        Domain validation happens before the request is sent.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            url: Full URL to call. Must be in allowed_domains.
            json: JSON body (mutually exclusive with data).
            data: Raw bytes body.
            headers: Additional headers (auth headers are added automatically).
            timeout: Request timeout in seconds.

        Returns:
            AuthorizedResponse with status_code, headers, and body.

        Raises:
            PermissionError: If the target domain is not allowed.
        """
        import httpx

        if not self.is_domain_allowed(url):
            try:
                host = urlparse(url).hostname or "unknown"
            except Exception:
                host = "unknown"
            raise PermissionError(
                f"CredentialHandle for '{self._provider}' cannot access "
                f"host '{host}'. Allowed domains: {self._allowed_domains}"
            )

        # Build merged headers — auth headers injected internally
        merged = dict(headers or {})
        merged.update(self._build_auth_headers())

        resp = httpx.request(
            method,
            url,
            headers=merged,
            json=json,
            content=data,
            timeout=timeout,
        )

        return AuthorizedResponse(
            status_code=resp.status_code,
            headers=dict(resp.headers),
            body=resp.text,
        )

    # --- Serialization prevention (S12) ---

    def __getstate__(self) -> Any:
        raise TypeError(
            "CredentialHandle is not serializable. "
            "Secrets must not be persisted outside the vault."
        )

    def __reduce__(self) -> Any:
        raise TypeError(
            "CredentialHandle cannot be pickled. "
            "Secrets must not be persisted outside the vault."
        )

    def __repr__(self) -> str:
        source_str = f", source={self._source!r}" if self._source else ""
        return (
            f"CredentialHandle(provider={self._provider!r}, "
            f"auth_type={self._auth_type!r}, "
            f"scopes={self._scopes!r}, "
            f"domains={self._allowed_domains!r}{source_str})"
        )

    def __str__(self) -> str:
        return f"CredentialHandle({self._provider})"
