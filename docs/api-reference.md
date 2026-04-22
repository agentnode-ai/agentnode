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
{ "email": "user@example.com", "username": "myuser", "password": "Secret123", "invite_code": "optional-code" }
```

The `invite_code` field is optional. Password must be at least 8 characters with one uppercase letter, one lowercase letter, and one digit.

**Response:** `201`
```json
{ "id": "uuid", "email": "user@example.com", "username": "myuser" }
```

### `POST /v1/auth/login`

```json
{ "email": "user@example.com", "password": "Secret123", "totp_code": "123456" }
```

The `totp_code` field is optional (required only when 2FA is enabled).

**Response:** `200`
```json
{ "access_token": "jwt...", "refresh_token": "jwt...", "token_type": "bearer" }
```

### `POST /v1/auth/refresh`

```json
{ "refresh_token": "jwt..." }
```

The `refresh_token` field is optional in the body when using httpOnly cookies (web clients). Token rotation: the old refresh token is consumed and a new pair is issued.

**Response:** `200`
```json
{ "access_token": "jwt...", "refresh_token": "jwt..." }
```

### `POST /v1/auth/logout`

Clears auth cookies and revokes the refresh token.

**Response:** `200`
```json
{ "message": "Logged out successfully." }
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

### `GET /v1/auth/api-keys`

Auth required. List all API keys for the current user.

**Response:** `200`
```json
{
  "keys": [
    { "id": "uuid", "key_prefix": "ank_xxxx", "label": "my-project", "created_at": "2026-03-14T...", "last_used_at": null }
  ]
}
```

### `DELETE /v1/auth/api-keys/{key_id}`

Auth required. Revoke an API key.

**Response:** `204` (no body)

### `GET /v1/auth/me`

Auth required.

**Response:** `200`
```json
{ "id": "uuid", "email": "user@example.com", "username": "myuser", "publisher": null, "two_factor_enabled": false, "is_admin": false }
```

### `POST /v1/auth/2fa/setup`

Auth required. Requires current password to prevent session hijack enrollment.

```json
{ "current_password": "Secret123" }
```

**Response:** `200`
```json
{ "secret": "TOTP_SECRET", "provisioning_uri": "otpauth://totp/..." }
```

### `POST /v1/auth/2fa/verify`

Auth required. Confirms 2FA setup with a TOTP code.

```json
{ "code": "123456" }
```

**Response:** `200`
```json
{ "two_factor_enabled": true }
```

### `POST /v1/auth/change-password`

Auth required.

```json
{ "current_password": "OldSecret123", "new_password": "NewSecret456" }
```

**Response:** `200`
```json
{ "message": "Password changed successfully" }
```

### `POST /v1/auth/request-password-reset`

```json
{ "email": "user@example.com" }
```

**Response:** `200`
```json
{ "message": "..." }
```

### `POST /v1/auth/reset-password`

```json
{ "token": "reset-token-from-email", "new_password": "NewSecret456" }
```

**Response:** `200`
```json
{ "message": "..." }
```

### `POST /v1/auth/email/request-verification`

Auth required. Sends a verification email.

**Response:** `200`
```json
{ "message": "..." }
```

### `POST /v1/auth/email/verify`

```json
{ "token": "verification-token-from-email" }
```

**Response:** `200`
```json
{ "email_verified": true }
```

### `PUT /v1/auth/profile`

Auth required. Update username or email. `current_password` required when changing email.

```json
{ "username": "newname", "email": "new@example.com", "current_password": "Secret123" }
```

**Response:** `200`
```json
{ "id": "uuid", "email": "new@example.com", "username": "newname", "email_verified": false }
```

---

## Publishers

### `POST /v1/publishers`

Auth required.

```json
{ "display_name": "My Publisher", "slug": "my-publisher", "bio": "optional", "website_url": "https://example.com", "github_url": "https://github.com/my-org" }
```

**Response:** `201`
```json
{
  "id": "uuid",
  "display_name": "My Publisher",
  "slug": "my-publisher",
  "bio": "optional",
  "trust_level": "unverified",
  "website_url": "https://example.com",
  "github_url": "https://github.com/my-org",
  "packages_published_count": 0,
  "created_at": "2026-03-14T..."
}
```

### `GET /v1/publishers/{slug}`

**Response:** `200`
```json
{
  "id": "uuid",
  "display_name": "My Publisher",
  "slug": "my-publisher",
  "bio": "...",
  "trust_level": "unverified",
  "website_url": "https://example.com",
  "github_url": "https://github.com/my-org",
  "packages_published_count": 3,
  "created_at": "2026-03-14T..."
}
```

### `PUT /v1/publishers/{slug}`

Auth required, must own publisher. Update publisher profile.

```json
{ "display_name": "New Name", "bio": "Updated bio", "website_url": "https://new.com", "github_url": "https://github.com/new-org" }
```

All fields are optional.

**Response:** `200` (same shape as `GET /v1/publishers/{slug}`)

### `PUT /v1/publishers/{slug}/signing-key`

Auth required, must own publisher. Register or replace an Ed25519 signing public key.

```json
{ "public_key": "base64-encoded-32-byte-ed25519-public-key" }
```

**Response:** `200`
```json
{ "public_key": "base64...", "registered_at": "2026-03-14T..." }
```

### `GET /v1/publishers/{slug}/signing-key`

Public endpoint. Get the publisher's signing public key.

**Response:** `200`
```json
{ "public_key": "base64...", "registered_at": "2026-03-14T..." }
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

Auth required + publisher profile. Content-Type: `multipart/form-data`.

Fields: `manifest` (JSON string), `artifact` (.tar.gz file, optional).

**Response:** `201`
```json
{ "slug": "my-pack", "version": "1.0.0", "package_type": "toolpack", "message": "Published my-pack@1.0.0" }
```

### `GET /v1/packages/{slug}`

Full detail response with blocks. Supports `?v=1.0.0` query parameter to load a specific version.

**Response:** `200`
```json
{
  "slug": "pdf-reader-pack",
  "name": "PDF Reader Pack",
  "package_type": "toolpack",
  "summary": "Extract text and tables from PDF files.",
  "description": "...",
  "publisher": { "slug": "agentnode", "display_name": "AgentNode", "trust_level": "unverified" },
  "latest_version": {
    "version_number": "1.0.0",
    "channel": "stable",
    "published_at": "2026-03-14T...",
    "security_reviewed_at": null,
    "compatibility_reviewed_at": null,
    "manually_reviewed_at": null
  },
  "download_count": 42,
  "install_count": 10,
  "is_deprecated": false,
  "quarantine_status": null,
  "blocks": {
    "capabilities": [{
      "name": "extract_pdf_text",
      "capability_id": "pdf_extraction",
      "capability_type": "tool",
      "description": "Extract text from PDF files",
      "entrypoint": "pdf_reader_pack.extract",
      "input_schema": null,
      "output_schema": null
    }],
    "prompts": [],
    "resources": [],
    "connector": null,
    "recommended_for": [{ "agent_type": "legal-assistant", "missing_capability": "pdf_extraction" }],
    "install": {
      "cli_command": "agentnode install pdf-reader-pack",
      "sdk_code": "...",
      "entrypoint": "pdf_reader_pack.tool",
      "post_install_code": "...",
      "installable_version": "1.0.0",
      "install_resolution": "latest_stable"
    },
    "compatibility": {
      "frameworks": ["generic"],
      "runtime": "python",
      "python": ">=3.10",
      "dependencies": []
    },
    "permissions": {
      "network_level": "none",
      "filesystem_level": "temp",
      "code_execution_level": "none",
      "data_access_level": "input_only",
      "user_approval_level": "never"
    },
    "performance": { "download_count": 42, "install_count": 10, "review_count": 5, "avg_rating": 4.2 },
    "trust": {
      "publisher_trust_level": "unverified",
      "signature_present": false,
      "provenance_present": false,
      "security_findings_count": 0,
      "verification_status": null,
      "last_updated": null
    }
  },
  "license_model": null,
  "readme_md": null,
  "file_list": null,
  "env_requirements": null,
  "use_cases": null,
  "examples": null,
  "tags": ["pdf", "extraction"],
  "homepage_url": null,
  "docs_url": null,
  "source_url": null,
  "verification": null
}
```

### `GET /v1/packages/{slug}/versions`

Paginated list of non-yanked, non-quarantined versions.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | int | 1 | Page number (min 1) |
| `per_page` | int | 50 | Results per page (max 100) |

**Response:** `200`
```json
{
  "versions": [{
    "version_number": "1.0.0",
    "channel": "stable",
    "changelog": "...",
    "published_at": "2026-03-14T...",
    "verification_status": "verified"
  }],
  "total": 1,
  "page": 1,
  "per_page": 50
}
```

### `GET /v1/packages/{slug}/versions/all`

Auth required, must own package. Returns ALL versions including yanked and quarantined.

**Response:** `200`
```json
{
  "versions": [{
    "version_number": "1.0.0",
    "channel": "stable",
    "changelog": "...",
    "published_at": "2026-03-14T...",
    "quarantine_status": "none",
    "is_yanked": false,
    "verification_status": "verified"
  }]
}
```

### `PATCH /v1/packages/{slug}`

Auth required, must own package. Edit package metadata (name, summary, description, tags).

```json
{ "name": "New Name", "summary": "Updated summary", "description": "...", "tags": ["pdf", "extraction"] }
```

All fields are optional.

**Response:** `200`
```json
{ "ok": true, "message": "" }
```

### `POST /v1/packages/{slug}/deprecate`

Auth required, must own package.

**Response:** `200`
```json
{ "ok": true, "message": "Package deprecated" }
```

### `POST /v1/packages/{slug}/versions/{version}/yank`

Auth required, must own package.

**Response:** `200`
```json
{ "ok": true, "message": "Version yanked" }
```

### `POST /v1/packages/{slug}/request-reverify`

Auth required, must own package. Re-runs verification on the latest owner-visible version. 1-hour cooldown.

**Response:** `200`
```json
{ "message": "Verification requested", "version": "1.0.0" }
```

---

## Search

### `POST /v1/search`

Full-text search over published packages. Uses a JSON request body (not query parameters).

**Request body:**
```json
{
  "q": "pdf",
  "package_type": "toolpack",
  "capability_id": "pdf_extraction",
  "framework": "langchain",
  "runtime": "python",
  "trust_level": "verified",
  "verification_tier": "gold",
  "tag": "pdf",
  "publisher_slug": "agentnode",
  "sort_by": "download_count:desc",
  "page": 1,
  "per_page": 20
}
```

All fields are optional except defaults.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `q` | string | `""` | Search query (max 256 chars) |
| `package_type` | string | null | Filter by package type |
| `capability_id` | string | null | Filter by capability ID |
| `framework` | string | null | Filter by framework |
| `runtime` | string | null | Filter by runtime |
| `trust_level` | string | null | Filter by trust level |
| `verification_tier` | string | null | Filter by verification tier |
| `tag` | string | null | Filter by tag |
| `publisher_slug` | string | null | Filter by publisher slug |
| `sort_by` | string | null | Sort order (see below) |
| `page` | int | 1 | Page number (1-500) |
| `per_page` | int | 20 | Results per page (1-100) |

Allowed `sort_by` values: `download_count:desc`, `download_count:asc`, `install_count:desc`, `install_count:asc`, `published_at:desc`, `published_at:asc`, `name:asc`, `name:desc`. When no `sort_by` and no query text, defaults to `download_count:desc`.

**Response:** `200`
```json
{
  "query": "pdf",
  "hits": [{
    "slug": "pdf-reader-pack",
    "name": "PDF Reader Pack",
    "package_type": "toolpack",
    "summary": "Extract text and tables from PDF files.",
    "publisher_name": "AgentNode",
    "publisher_slug": "agentnode",
    "trust_level": "unverified",
    "latest_version": "1.0.0",
    "runtime": "python",
    "capability_ids": ["pdf_extraction"],
    "tags": ["pdf"],
    "frameworks": ["generic"],
    "download_count": 42,
    "install_count": 10,
    "is_deprecated": false,
    "verification_status": "verified",
    "verification_score": 85,
    "verification_tier": "gold"
  }],
  "total": 1,
  "page": 1,
  "per_page": 20
}
```

---

## Resolution

### `POST /v1/resolve`

Auth required. Resolve requested capabilities to ranked packages.

```json
{
  "capabilities": ["pdf_extraction", "web_search"],
  "framework": "langchain",
  "runtime": "python",
  "package_type": "toolpack",
  "limit": 10,
  "policy": { "min_trust": "verified", "allow_shell": false, "allow_network": true }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `capabilities` | list[str] | yes | Capability IDs to resolve (min 1) |
| `framework` | string | no | Filter by framework |
| `runtime` | string | no | Filter by runtime |
| `package_type` | string | no | Filter by package type |
| `limit` | int | no | Max results (1-50, default 10) |
| `policy` | object | no | Policy constraints |

**Response:** `200`
```json
{
  "results": [{
    "slug": "pdf-reader-pack",
    "name": "PDF Reader Pack",
    "package_type": "toolpack",
    "summary": "Extract text and tables from PDF files.",
    "version": "1.0.0",
    "publisher_slug": "agentnode",
    "trust_level": "unverified",
    "score": 0.85,
    "policy_result": "allowed",
    "breakdown": { "capability": 1.0, "framework": 0.8, "runtime": 1.0, "trust": 0.5, "permissions": 1.0 },
    "matched_capabilities": ["pdf_extraction"],
    "broad_package": false
  }],
  "total": 1
}
```

### `POST /v1/resolve-upgrade`

Auth required. Find upgrade packages for current capabilities.

```json
{
  "current_capabilities": ["pdf_extraction"],
  "framework": "langchain",
  "runtime": "python",
  "policy": { "min_trust": "verified", "allow_shell": false, "allow_network": true }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `current_capabilities` | list[str] | yes | Capability IDs the agent already has (min 1) |
| `framework` | string | no | Filter by framework |
| `runtime` | string | no | Filter by runtime |
| `policy` | object | no | Policy constraints |

**Response:** `200`
```json
{
  "recommended": [{
    "package_slug": "pdf-reader-pack",
    "package_name": "PDF Reader Pack",
    "version": "1.0.0",
    "compatibility_score": 0.85,
    "trust_level": "unverified",
    "risk_level": "low",
    "policy_result": "allowed",
    "policy_reasons": [],
    "install_command": "agentnode install pdf-reader-pack",
    "dependencies": []
  }]
}
```

### `POST /v1/recommend`

Auth required. Broader discovery than `/resolve`: related capabilities, category-based suggestions, reasoning.

```json
{
  "missing_capabilities": ["pdf_extraction", "web_search"],
  "installed_packages": ["existing-pack"],
  "agent_description": "A legal research assistant",
  "framework": "langchain",
  "runtime": "python",
  "limit": 10
}
```

**Response:** `200`
```json
{
  "recommendations": [{
    "capability_id": "pdf_extraction",
    "source": "requested",
    "packages": [{
      "slug": "pdf-reader-pack",
      "name": "PDF Reader Pack",
      "version": "1.0.0",
      "compatibility_score": 0.85,
      "trust_level": "unverified",
      "reason": "Provides pdf_extraction capability",
      "also_provides": ["ocr"],
      "install_command": "agentnode install pdf-reader-pack"
    }]
  }],
  "total_packages": 1
}
```

### `POST /v1/check-policy`

Auth required. Check if a package passes policy constraints.

```json
{ "package_slug": "pdf-reader-pack", "framework": "langchain", "runtime": "python", "policy": { "min_trust": "verified", "allow_shell": false, "allow_network": true } }
```

**Response:** `200`
```json
{
  "result": "allowed",
  "reasons": [],
  "package_permissions": {
    "network_level": "none",
    "filesystem_level": "none",
    "code_execution_level": "none",
    "data_access_level": "input_only",
    "user_approval_level": "never"
  },
  "package_trust_level": "unverified"
}
```

### `GET /v1/capabilities`

Public endpoint. List all capabilities in the taxonomy.

| Parameter | Type | Description |
|-----------|------|-------------|
| `category` | string | Filter by category (optional) |

**Response:** `200`
```json
{
  "capabilities": [{
    "id": "pdf_extraction",
    "display_name": "PDF Extraction",
    "description": "...",
    "category": "document",
    "package_count": 3
  }],
  "total": 1
}
```

---

## Install

### `GET /v1/packages/{slug}/install-info`

Get install metadata (no side effects). Defaults to latest stable version.

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
  "artifact": {
    "url": "https://presigned-url...",
    "hash_sha256": "abc123...",
    "size_bytes": 12345,
    "expires_in_seconds": 900
  },
  "capabilities": [{
    "name": "extract_pdf_text",
    "capability_id": "pdf_extraction",
    "capability_type": "tool",
    "entrypoint": "pdf_reader_pack.extract"
  }],
  "dependencies": [{
    "package_slug": "ocr-pack",
    "role": "required",
    "is_required": true,
    "min_version": "0.5.0"
  }],
  "permissions": {
    "network_level": "none",
    "filesystem_level": "temp",
    "code_execution_level": "none",
    "data_access_level": "input_only",
    "user_approval_level": "never",
    "allowed_domains": [],
    "external_integrations": []
  },
  "published_at": "2026-03-14T...",
  "verification_status": "verified",
  "verification_tier": "gold",
  "verification_score": 85,
  "install_resolution": "latest_stable",
  "agent": null
}
```

### `POST /v1/packages/{slug}/install`

Auth required. Create an installation record and return artifact URL.

```json
{ "version": "1.0.0", "source": "cli", "event_type": "install", "installation_context": {} }
```

All fields are optional (defaults: `source="cli"`, `event_type="install"`).

**Response:** `200`
```json
{
  "package_slug": "pdf-reader-pack",
  "version": "1.0.0",
  "install_strategy": "local",
  "artifact_url": "https://presigned-url...",
  "artifact_hash": "sha256:...",
  "entrypoint": "pdf_reader_pack.tool",
  "post_install_code": "from pdf_reader_pack import tool",
  "installation_id": "uuid",
  "deprecated": false,
  "tools": [{ "name": "extract_pdf_text", "entrypoint": "pdf_reader_pack.extract", "capability_id": "pdf_extraction" }],
  "verification_status": "verified",
  "verification_tier": "gold",
  "verification_score": 85,
  "install_resolution": "latest_stable",
  "agent": null
}
```

### `POST /v1/packages/{slug}/download`

Track download and get presigned artifact URL. Version is passed as a query parameter.

| Parameter | Type | Description |
|-----------|------|-------------|
| `version` | string | Specific version (optional query param) |

**Response:** `200`
```json
{
  "slug": "pdf-reader-pack",
  "version": "1.0.0",
  "download_url": "https://presigned-url...",
  "download_count": 43,
  "artifact_hash_sha256": "abc123...",
  "artifact_size_bytes": 12345,
  "verification_tier": "gold",
  "install_resolution": "latest_stable"
}
```

### `POST /v1/packages/check-updates`

Auth required. Batch check for available updates.

```json
{ "packages": [{ "slug": "pdf-reader-pack", "version": "1.0.0" }] }
```

Max 100 packages per request.

**Response:** `200`
```json
{
  "updates": [{
    "slug": "pdf-reader-pack",
    "current_version": "1.0.0",
    "latest_version": "1.1.0",
    "latest_published_version": "1.2.0",
    "has_update": true,
    "verification_tier": "gold",
    "install_resolution": "latest_stable"
  }]
}
```

### `GET /v1/installations`

Auth required. List current user's installations.

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter by status (optional): `installed`, `active`, `inactive`, `uninstalled` |

**Response:** `200`
```json
{
  "installations": [{
    "id": "uuid",
    "package_slug": "pdf-reader-pack",
    "version": "1.0.0",
    "status": "active",
    "source": "cli",
    "event_type": "install",
    "installed_at": "2026-03-14T...",
    "activated_at": "2026-03-14T...",
    "uninstalled_at": null
  }],
  "total": 1
}
```

### `POST /v1/installations/{id}/activate`

Auth required. Mark an installation as active.

**Response:** `200`
```json
{ "activated": true }
```

### `POST /v1/installations/{id}/uninstall`

Auth required. Mark an installation as uninstalled.

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
  "signature_verified": false,
  "provenance_present": true,
  "provenance_verified": true,
  "source_repo": "https://github.com/agentnode/pdf-reader-pack",
  "security_findings_count": 0,
  "open_findings": [],
  "quarantine_status": "none",
  "last_scan_at": null
}
```

---

## Reviews

### `POST /v1/packages/{slug}/reviews`

Auth required. Must have installed the package.

```json
{ "rating": 5, "comment": "Works great for extracting PDF tables." }
```

Rating: 1-5. Comment: max 1000 characters, optional.

**Response:** `201`
```json
{ "id": "uuid" }
```

### `GET /v1/packages/{slug}/reviews`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | int | 1 | Page number |
| `per_page` | int | 50 | Results per page (max 100) |

**Response:** `200`
```json
{ "reviews": [{ "username": "marco", "rating": 5, "comment": "...", "created_at": "..." }], "avg_rating": 4.5, "total": 3, "page": 1, "per_page": 50 }
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

## Credentials

### `POST /v1/credentials/`

Auth required. Store an encrypted credential for a connector package. Requires publisher trust >= verified.

```json
{
  "connector_package_slug": "slack-connector",
  "connector_provider": "slack",
  "auth_type": "api_key",
  "secret_data": { "api_key": "xoxb-..." },
  "scopes": ["chat:write"]
}
```

**Response:** `201`
```json
{ "id": "uuid", "connector_provider": "slack", "status": "active", "message": "Credential stored for slack" }
```

### `GET /v1/credentials/`

Auth required. List all credentials for the current user. No secrets returned.

**Response:** `200`
```json
{
  "credentials": [{
    "id": "uuid",
    "connector_provider": "slack",
    "connector_package_slug": "slack-connector",
    "auth_type": "api_key",
    "scopes": ["chat:write"],
    "allowed_domains": ["slack.com"],
    "status": "active",
    "created_at": "2026-03-14T...",
    "last_used_at": null,
    "expires_at": null
  }]
}
```

### `DELETE /v1/credentials/{credential_id}`

Auth required. Revoke a credential.

**Response:** `204` (no body)

### `POST /v1/credentials/{id}/test`

Auth required. Test connectivity for a stored credential.

**Response:** `200`
```json
{ "reachable": true, "status_code": 200, "latency_ms": 142.5, "message": "OK" }
```

### `POST /v1/credentials/oauth/initiate`

Auth required. Start an OAuth2 PKCE authorization flow.

```json
{ "connector_package_slug": "slack-connector", "scopes": ["chat:write", "channels:read"] }
```

**Response:** `200`
```json
{ "auth_url": "https://slack.com/oauth/v2/authorize?client_id=...&code_challenge=...&state=...", "state": "random-state-token" }
```

Supported providers: GitHub, Slack. Provider is determined from the connector manifest.

### `GET /v1/credentials/oauth/callback`

OAuth2 callback. Exchanges authorization code for tokens, encrypts and stores them.

| Parameter | Type | Description |
|-----------|------|-------------|
| `code` | string | Authorization code from provider |
| `state` | string | State token (must match initiate) |

Redirects to frontend with success/error status.

### `GET /v1/credentials/resolve/{provider}`

Auth required. Get a short-lived resolve token for credential proxy access.

**Response:** `200`
```json
{ "resolve_token": "eyJhbGc...", "provider": "github", "allowed_domains": ["api.github.com"] }
```

The token is a JWT with 60-second TTL. Use it with the proxy endpoint below.

### `POST /v1/credentials/proxy`

Execute an HTTP request with server-side credential injection.

**Request** (`ProxyRequest` -- Pydantic-validated):
```json
{
  "resolve_token": "eyJhbGc...",
  "method": "GET",
  "url": "https://api.github.com/user",
  "json_body": null
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `resolve_token` | string | yes | JWT from `/resolve/{provider}` |
| `method` | string | no | `GET`, `POST`, `PUT`, `PATCH`, `DELETE` (default: `GET`) |
| `url` | string | yes | Target URL (must match connector's `allowed_domains`) |
| `json_body` | object | no | JSON body for POST/PUT/PATCH requests |

Invalid payloads return HTTP 422 with Pydantic validation details.

**Response:** `200`
```json
{ "status_code": 200, "body": { "login": "octocat", "id": 1 } }
```

Domain validation applies -- the URL must match the connector's `allowed_domains`.
No credential secrets appear in the response.

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
