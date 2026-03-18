# API Reference

Base URL: `https://agentnode.net/v1`

Authentication: Bearer JWT token or `X-API-Key: ank_...` header.

## Health

### `GET /healthz`

Basic health check.

**Response:** `200`
```json
{ "status": "ok" }
```

### `GET /readyz`

Readiness check (Postgres, Redis, Meilisearch).

**Response:** `200`
```json
{ "status": "ready", "details": { "postgres": "ok", "redis": "ok", "meilisearch": "ok" } }
```

---

## Auth

### `POST /v1/auth/register`

```json
{ "email": "user@example.com", "username": "myuser", "password": "secret123" }
```

**Response:** `201`
```json
{ "id": "uuid", "email": "user@example.com", "username": "myuser" }
```

### `POST /v1/auth/login`

```json
{ "email": "user@example.com", "password": "secret123", "totp_code": "123456" }
```

**Response:** `200`
```json
{ "access_token": "jwt...", "refresh_token": "jwt...", "token_type": "bearer" }
```

### `POST /v1/auth/refresh`

```json
{ "refresh_token": "jwt..." }
```

**Response:** `200`
```json
{ "access_token": "jwt..." }
```

### `POST /v1/auth/api-keys`

Auth required.

```json
{ "label": "my-project" }
```

**Response:** `201`
```json
{ "id": "uuid", "key": "ank_full_key", "key_prefix": "ank_xxxx", "label": "my-project" }
```

> The full key is returned only at creation. Store it securely.

### `GET /v1/auth/me`

Auth required.

**Response:** `200`
```json
{ "id": "uuid", "email": "user@example.com", "username": "myuser", "publisher": null, "two_factor_enabled": false }
```

### `POST /v1/auth/2fa/setup`

Auth required.

**Response:** `200`
```json
{ "secret": "TOTP_SECRET", "qr_uri": "otpauth://totp/..." }
```

### `POST /v1/auth/2fa/verify`

Auth required.

```json
{ "totp_code": "123456" }
```

**Response:** `200`
```json
{ "two_factor_enabled": true }
```

---

## Publishers

### `POST /v1/publishers`

Auth required.

```json
{ "display_name": "My Publisher", "slug": "my-publisher", "bio": "optional", "website_url": "https://example.com" }
```

**Response:** `201`
```json
{ "id": "uuid", "slug": "my-publisher", "display_name": "My Publisher", "trust_level": "unverified" }
```

### `GET /v1/publishers/{slug}`

**Response:** `200`
```json
{ "id": "uuid", "display_name": "My Publisher", "slug": "my-publisher", "bio": "...", "trust_level": "unverified", "package_count": 3, "created_at": "2026-03-14T..." }
```

---

## Packages

### `POST /v1/packages/validate`

Auth required.

```json
{ "manifest": { "...agentnode.yaml fields as JSON..." } }
```

**Response:** `200`
```json
{ "valid": true, "errors": [], "warnings": [] }
```

### `POST /v1/packages/publish`

Auth required + 2FA enabled. Content-Type: `multipart/form-data`.

Fields: `manifest` (JSON string), `artifact` (.tar.gz file, max 50MB).

**Response:** `201`
```json
{ "package_id": "uuid", "package_slug": "my-pack", "version_id": "uuid", "version_number": "1.0.0", "artifact_hash": "sha256:..." }
```

### `GET /v1/packages/{slug}`

7-block detail response.

**Response:** `200`
```json
{
  "slug": "pdf-reader-pack",
  "name": "PDF Reader Pack",
  "package_type": "toolpack",
  "summary": "Extract text and tables from PDF files.",
  "description": "...",
  "publisher": { "slug": "agentnode", "display_name": "AgentNode", "trust_level": "unverified" },
  "latest_version": { "version_number": "1.0.0", "channel": "stable", "published_at": "..." },
  "download_count": 42,
  "is_deprecated": false,
  "blocks": {
    "capabilities": [{ "name": "extract_pdf_text", "capability_id": "pdf_extraction", "description": "..." }],
    "recommended_for": [{ "agent_type": "legal-assistant", "missing_capability": "pdf_extraction" }],
    "install": { "cli_command": "agentnode install pdf-reader-pack", "entrypoint": "pdf_reader_pack.tool" },
    "compatibility": { "frameworks": ["generic"], "python": ">=3.10" },
    "permissions": { "network_level": "none", "filesystem_level": "temp", "code_execution_level": "none" },
    "performance": { "download_count": 42, "review_count": 5, "avg_rating": 4.2 },
    "trust": { "publisher_trust_level": "unverified", "signature_present": false, "security_findings_count": 0 }
  }
}
```

### `GET /v1/packages/{slug}/versions`

**Response:** `200`
```json
{ "versions": [{ "version_number": "1.0.0", "channel": "stable", "changelog": "...", "published_at": "..." }] }
```

### `POST /v1/packages/{slug}/deprecate`

Auth required, must own package.

**Response:** `200`
```json
{ "deprecated": true }
```

### `POST /v1/packages/{slug}/versions/{version}/yank`

Auth required, must own package.

**Response:** `200`
```json
{ "yanked": true }
```

---

## Search

### `GET /v1/search`

| Parameter | Type | Description |
|-----------|------|-------------|
| `q` | string | Search query |
| `capability` | string | Filter by capability ID |
| `package_type` | string | Filter by type |
| `framework` | string | Filter by framework |
| `sort_by` | string | `relevance`, `downloads`, `recent` |
| `limit` | int | Results per page (default 20, max 100) |
| `offset` | int | Pagination offset |

**Response:** `200`
```json
{
  "hits": [{ "slug": "pdf-reader-pack", "name": "PDF Reader Pack", "summary": "...", "trust_level": "unverified", "latest_version": "1.0.0", "download_count": 42 }],
  "query": "pdf",
  "total": 1
}
```

---

## Resolution

### `POST /v1/resolve-upgrade`

Auth required.

```json
{
  "missing_capability": "pdf_extraction",
  "framework": "langchain",
  "runtime": "python",
  "current_capabilities": [],
  "policy": { "min_trust": "verified", "allow_shell": false, "allow_network": true }
}
```

**Response:** `200`
```json
{
  "resolution_id": "uuid",
  "missing_capability": "pdf_extraction",
  "recommended": [{
    "package_slug": "pdf-reader-pack",
    "package_name": "PDF Reader Pack",
    "version": "1.0.0",
    "compatibility_score": 0.85,
    "trust_level": "unverified",
    "risk_level": "low",
    "policy_result": "allowed",
    "install_command": "agentnode install pdf-reader-pack",
    "dependencies": []
  }]
}
```

### `POST /v1/recommend`

Auth required.

```json
{ "missing_capabilities": ["pdf_extraction", "web_search"], "framework": "langchain" }
```

**Response:** `200`
```json
{
  "recommendations": [{
    "capability_id": "pdf_extraction",
    "packages": [{ "slug": "pdf-reader-pack", "name": "PDF Reader Pack", "compatibility_score": 0.85, "trust_level": "unverified" }]
  }]
}
```

### `POST /v1/check-policy`

Auth required.

```json
{ "package_slug": "pdf-reader-pack", "framework": "langchain", "policy": { "min_trust": "verified", "allow_shell": false } }
```

**Response:** `200`
```json
{ "result": "allowed", "reasons": [], "package_permissions": { "network_level": "none", "code_execution_level": "none" }, "package_trust_level": "unverified" }
```

---

## Install

### `GET /v1/packages/{slug}/install`

Get install metadata (no side effects).

| Parameter | Type | Description |
|-----------|------|-------------|
| `version` | string | Specific version (optional) |

**Response:** `200`
```json
{
  "slug": "pdf-reader-pack",
  "version": "1.0.0",
  "package_type": "toolpack",
  "install_mode": "package",
  "hosting_type": "agentnode_hosted",
  "runtime": "python",
  "entrypoint": "pdf_reader_pack.tool",
  "artifact": { "url": "https://presigned-url...", "hash_sha256": "abc123...", "size_bytes": 12345 },
  "capabilities": [{ "name": "extract_pdf_text", "capability_id": "pdf_extraction" }],
  "permissions": { "network_level": "none", "filesystem_level": "temp" }
}
```

### `POST /v1/packages/{slug}/download`

Track install and get download URL.

```json
{ "version": "1.0.0", "event_type": "install", "source": "cli" }
```

**Response:** `200`
```json
{ "download_url": "https://presigned-url...", "hash_sha256": "abc123..." }
```

### `POST /v1/packages/check-updates`

Auth required.

```json
{ "packages": [{ "slug": "pdf-reader-pack", "version": "1.0.0" }] }
```

**Response:** `200`
```json
{ "updates": [{ "slug": "pdf-reader-pack", "current_version": "1.0.0", "latest_version": "1.1.0", "has_update": true }] }
```

### `POST /v1/installations/{id}/activate`

Auth required.

**Response:** `200`
```json
{ "activated": true }
```

### `POST /v1/installations/{id}/uninstall`

Auth required.

**Response:** `200`
```json
{ "uninstalled": true }
```

---

## Trust

### `GET /v1/packages/{slug}/trust`

**Response:** `200`
```json
{
  "publisher_trust_level": "unverified",
  "publisher_slug": "agentnode",
  "signature_present": false,
  "provenance_present": true,
  "security_findings_count": 0,
  "open_findings": [],
  "quarantine_status": "none"
}
```

---

## Reviews

### `POST /v1/packages/{slug}/reviews`

Auth required. Must have installed the package.

```json
{ "rating": 5, "comment": "Works great for extracting PDF tables." }
```

**Response:** `201`
```json
{ "id": "uuid" }
```

### `GET /v1/packages/{slug}/reviews`

**Response:** `200`
```json
{ "reviews": [{ "username": "marco", "rating": 5, "comment": "...", "created_at": "..." }], "avg_rating": 4.5, "total": 3 }
```

---

## Reports

### `POST /v1/packages/{slug}/report`

Auth required.

```json
{ "reason": "malware", "description": "Found suspicious code execution..." }
```

Valid reasons: `malware`, `typosquatting`, `spam`, `misleading`, `policy_violation`, `other`.

**Response:** `201`
```json
{ "report_id": "uuid", "status": "submitted" }
```

---

## Error Format

All errors follow this format:

```json
{
  "error": {
    "code": "PACKAGE_NOT_FOUND",
    "message": "Package 'nonexistent-pack' not found"
  }
}
```

Common error codes:
| Code | HTTP | Description |
|------|------|-------------|
| `AUTH_REQUIRED` | 401 | Missing or invalid authentication |
| `AUTH_2FA_REQUIRED` | 403 | 2FA required for this action |
| `PACKAGE_NOT_FOUND` | 404 | Package does not exist |
| `PACKAGE_VERSION_EXISTS` | 409 | Version already published |
| `PACKAGE_SLUG_TAKEN` | 409 | Slug owned by another publisher |
| `PACKAGE_YANKED` | 410 | Version has been yanked |
| `PACKAGE_QUARANTINED` | 403 | Version is quarantined |
| `CAPABILITY_ID_UNKNOWN` | 422 | Unknown capability ID |
| `MANIFEST_INVALID` | 422 | Manifest validation failed |
| `RATE_LIMITED` | 429 | Too many requests |
