"""Pydantic schemas for credential CRUD — secrets never in responses."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class StoreCredentialRequest(BaseModel):
    """Request to store a credential for a connector.

    The caller provides the raw secret (api_key or oauth tokens).
    The service encrypts it before storage.
    """

    connector_package_slug: str
    connector_provider: str
    auth_type: str  # "api_key" or "oauth2"
    secret_data: dict  # Raw secret — encrypted server-side, NEVER stored in plain text
    scopes: list[str] = []


class CredentialSummary(BaseModel):
    """Credential listing — NO secrets, only metadata."""

    id: UUID
    connector_provider: str
    connector_package_slug: str
    auth_type: str
    scopes: list[str]
    allowed_domains: list[str]
    status: str
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None


class CredentialListResponse(BaseModel):
    credentials: list[CredentialSummary]


class CredentialStoreResponse(BaseModel):
    id: UUID
    connector_provider: str
    status: str
    message: str


class CredentialTestResponse(BaseModel):
    """Result of a connectivity test against the connector's health endpoint."""

    reachable: bool
    status_code: int | None = None
    latency_ms: float | None = None
    message: str


class OAuthInitiateRequest(BaseModel):
    """Request to start an OAuth2 PKCE flow."""

    connector_package_slug: str
    scopes: list[str] = []


class OAuthInitiateResponse(BaseModel):
    """Response with the authorization URL for the OAuth2 flow."""

    auth_url: str
    state: str


# ---------------------------------------------------------------------------
# Proxy & Resolve (v0.5 — typed bodies)
# ---------------------------------------------------------------------------

class ProxyRequest(BaseModel):
    """Request to proxy an authenticated API call through the backend."""

    resolve_token: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str
    json_body: dict | None = None


class ProxyResponse(BaseModel):
    """Response from a proxied API call."""

    status_code: int
    body: Any


class ResolveCredentialResponse(BaseModel):
    """Response from the credential resolve endpoint."""

    resolve_token: str
    provider: str
    allowed_domains: list[str]
