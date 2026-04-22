# AgentNode API — Error Codes Reference

All API errors follow this format:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable description",
    "details": {}
  }
}
```

## Authentication Errors (401/403)

| Code | HTTP | Description |
|------|------|-------------|
| `AUTH_INVALID_CREDENTIALS` | 401 | Invalid email/password, missing/expired token |
| `AUTH_TOKEN_EXPIRED` | 401 | Access or refresh token has expired |
| `AUTH_API_KEY_INVALID` | 401 | API key not found |
| `AUTH_API_KEY_REVOKED` | 401 | API key has been revoked |
| `AUTH_2FA_REQUIRED` | 403 | 2FA code required for login |
| `AUTH_2FA_INVALID` | 403 | Invalid 2FA/TOTP code |
| `AUTH_2FA_NOT_SETUP` | 400 | Must call `/2fa/setup` before verifying |
| `AUTH_2FA_ALREADY_ENABLED` | 400 | 2FA already enabled for user |
| `PUBLISHER_REQUIRED` | 403 | Must create a publisher profile first |
| `AUTH_ACCOUNT_LOCKED` | 429 | Too many failed login attempts. Check `details.retry_after` |
| `AUTH_ACCOUNT_BANNED` | 403 | Account has been banned |
| `AUTH_REAUTH_REQUIRED` | 403 | Re-authentication required for sensitive operation |
| `AUTH_TOKEN_INVALID` | 400 | Invalid or expired token (reset, verification) |
| `API_KEY_NOT_FOUND` | 404 | API key not found |
| `API_KEY_ALREADY_REVOKED` | 400 | API key was already revoked |
| `ADMIN_REQUIRED` | 403 | Admin privileges required |

## Registration Errors (409)

| Code | HTTP | Description |
|------|------|-------------|
| `AUTH_EMAIL_TAKEN` | 409 | Email is already registered |
| `AUTH_USERNAME_TAKEN` | 409 | Username is already taken |
| `AUTH_USER_NOT_FOUND` | 404 | User not found (internal) |

## Package Errors

| Code | HTTP | Description |
|------|------|-------------|
| `PACKAGE_NOT_FOUND` | 404 | Package slug does not exist |
| `PACKAGE_NOT_OWNED` | 403 | You are not the publisher of this package |
| `PACKAGE_VERSION_NOT_FOUND` | 404 | Specified version does not exist |
| `VERSION_NOT_FOUND` | 404 | Version not found |
| `VERSION_EXISTS` | 409 | Version number already published |
| `VERSION_YANKED` | 410 | Requested version has been yanked |
| `VERSION_QUARANTINED` | 403 | Version is under quarantine review |
| `NO_VERSION_AVAILABLE` | 404 | No installable version available |
| `MANIFEST_INVALID` | 422 | Manifest validation failed (see `details`) |
| `INVALID_STATE` | 409 | Cannot perform action in current state |

## Publishing Errors

| Code | HTTP | Description |
|------|------|-------------|
| `QUALITY_GATE_FAILED` | 422 | Artifact missing required test files (see `details`) |
| `PUBLISH_SIGNATURE_INVALID` | 400 | Package signature verification failed |
| `ARTIFACT_INVALID_TYPE` | 400 | Artifact must be a `.tar.gz` archive |
| `ARTIFACT_TOO_LARGE` | 413 | Artifact exceeds maximum size limit |

## Publisher Errors

| Code | HTTP | Description |
|------|------|-------------|
| `PUBLISHER_NOT_FOUND` | 404 | Publisher slug does not exist |
| `PUBLISHER_NOT_OWNED` | 403 | You are not the owner of this publisher |
| `PUBLISHER_ALREADY_EXISTS` | 409 | User already has a publisher profile |
| `PUBLISHER_SLUG_TAKEN` | 409 | Publisher slug is already in use |
| `PUBLISHER_SUSPENDED` | 403 | Publisher account is suspended |

## Installation Errors

| Code | HTTP | Description |
|------|------|-------------|
| `INSTALLATION_NOT_FOUND` | 404 | Installation ID does not exist |
| `INSTALLATION_NOT_OWNED` | 403 | You do not own this installation |

## Review & Report Errors

| Code | HTTP | Description |
|------|------|-------------|
| `REVIEW_NOT_ALLOWED` | 403 | Must install the package before reviewing |
| `REVIEW_EXISTS` | 409 | Already reviewed this package |
| `INVALID_REASON` | 400 | Report reason not in allowed set |

## Resolution & Capability Errors

| Code | HTTP | Description |
|------|------|-------------|
| `CAPABILITY_ID_UNKNOWN` | 422 | Unknown capability taxonomy ID |
| `CAPABILITY_EXISTS` | 409 | Capability already exists in taxonomy |
| `CAPABILITY_NOT_FOUND` | 404 | Capability not found in taxonomy |

## Admin Errors

| Code | HTTP | Description |
|------|------|-------------|
| `NOT_QUARANTINED` | 409 | Version is not currently quarantined |
| `ALREADY_SUSPENDED` | 409 | Publisher is already suspended |
| `NOT_SUSPENDED` | 409 | Publisher is not currently suspended |
| `REPORT_NOT_FOUND` | 404 | Report ID does not exist |

## Credential Errors

| Code | HTTP | Description |
|------|------|-------------|
| `CRED_NOT_FOUND` | 404 | Credential not found |
| `CRED_TRUST_TOO_LOW` | 403 | Publisher trust level too low for credential storage |
| `CRED_CONFIG_ERROR` | 500 | `CREDENTIAL_ENCRYPTION_KEY` not configured on server |

## OAuth Errors

| Code | HTTP | Description |
|------|------|-------------|
| `OAUTH_NOT_CONFIGURED` | 501 | OAuth provider not configured (missing client ID/secret) |
| `OAUTH_UNSUPPORTED_PROVIDER` | 400 | Unsupported OAuth provider |
| `OAUTH_INVALID_STATE` | 400 | Invalid or expired OAuth state parameter |

## Webhook Errors

| Code | HTTP | Description |
|------|------|-------------|
| `INVALID_EVENTS` | 422 | Invalid webhook event types provided |
| `WEBHOOK_NOT_FOUND` | 404 | Webhook ID does not exist or not owned |

## Rate Limiting

| Code | HTTP | Description |
|------|------|-------------|
| `RATE_LIMITED` | 429 | Too many requests. Check `details.retry_after` |

### Rate Limit Headers

All responses include:
- `X-RateLimit-Limit` — Max requests in window
- `X-RateLimit-Remaining` — Remaining requests
- `X-RateLimit-Reset` — Unix timestamp when window resets

### Default Limits

| Endpoint | Limit |
|----------|-------|
| Search | 30/min |
| Auth register | 10/min |
| Auth login | 10/min/IP + 5/min/email |
| Package publish | 10/min |
| Package install | 60/min |
| Download | 30/min |
| Resolve/Policy/Recommend | 60/min |
| Report | 10/hour |
| Webhook create | 10/min |
