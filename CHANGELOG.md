# Changelog

## v0.5.1 — Local Credentials (2026-04-15)

Use GitHub, Slack, and other connector packages **without an AgentNode account**.
Store your own tokens locally — like `gh auth login` or `aws configure`.

### Added

**Local Credential Storage**
- `agentnode auth <provider>` — store a Personal Access Token locally
  - Hidden input (token never echoed)
  - Validates token against provider API before saving
  - `--no-validate` for offline storage
- `agentnode auth list` — show stored credentials (never shows token values)
- `agentnode auth remove <provider>` — remove with safety prompt (`--yes` to skip)
- Credentials stored in `~/.agentnode/credentials.json` (plaintext + file permissions,
  same approach as gh, docker, aws)

**Credential Resolution Chain**
- SDK now resolves credentials in order: env var → local file → server API
- New `resolve_mode: "local"` config option (only check local file)
- Each `CredentialHandle` carries a `source` field (`"env"`, `"local_file"`, `"server"`)
  for debugging and logging

**Credential-Aware Install**
- `agentnode install <slug>`: if package needs credentials, prompts
  "Set up now? [y/N]" with inline auth flow
- Auto-install (`detect_and_install`/`smart_run`): skips packages without
  configured credentials by default, searches alternatives
- Config: `credentials.require_before_auto_install` (default: `true`)

### Notes

- **Existing server-side credentials continue to work unchanged**
- `agentnode credentials` remains for server-side/team credentials
- `agentnode auth` is the new default for personal/local use
- Backend `credentials/*` endpoints: zero changes
- Supported providers: GitHub, Slack

### FAQ

**I already use `agentnode credentials`. Do I need to change anything?**
No. Server-side credentials continue to work. Use `agentnode auth` if you want
local, account-free credentials for personal use.

**What's the difference between `agentnode auth` and `agentnode credentials`?**
- `agentnode auth` = local tokens, no account needed, stored on your machine
- `agentnode credentials` = server-side OAuth, requires AgentNode account

---

## v0.5.0 — Operational Completeness & Developer Surface (2026-04-14)

v0.5 makes v0.4 features accessible to real users and operationally safe under
production load. Nothing new was invented — every change closes a gap between
"feature exists internally" and "user can actually use it".

### Production Fixes

**OAuth State on Redis**
- OAuth pending states (`_pending_states`) moved from in-memory dict to Redis
  with 5-minute TTL via `oauth:state:{token}` keys
- Production (`ENVIRONMENT=production`): Redis required — unavailability returns
  HTTP 503 with clear message instead of silently losing OAuth flows
- Dev/test: automatic in-memory fallback with warning log
- `_cleanup_expired_states()` removed — Redis TTL handles expiration
- Dead code `_resolve_provider_from_manifest()` removed

**Run-Log Retention**
- `cleanup_old_runs(max_age_days=30, max_count=500)` deletes oldest run logs
  when either limit is exceeded
- Runs automatically after every agent execution (non-blocking, swallows errors)
- Configurable via `~/.agentnode/config.json`:
  ```json
  { "run_log": { "max_age_days": 30, "max_count": 500 } }
  ```
- Previously: `~/.agentnode/runs/` grew without limit

### New CLI Commands

**`agentnode runs`** — manage agent run logs (local, no backend needed)
- `agentnode runs list [--limit N]` — show recent runs (newest first)
- `agentnode runs show <run_id>` — display events for a specific run
- `agentnode runs clean [--dry-run] [--max-age <days>] [--max-count <n>]` — manual cleanup

**`agentnode credentials`** — manage stored credentials via API
- `agentnode credentials list` — show stored credentials (provider, status, domains)
- `agentnode credentials test <id>` — test credential connectivity
- `agentnode credentials delete <id>` — revoke a credential

All additive — no breaking changes to existing commands (CLI V1 policy).

### Dashboard

**Credentials Page** (`/dashboard/credentials`)
- Lists all stored credentials with provider, status, auth type, scopes, domains
- "Test Connection" button calls `POST /v1/credentials/{id}/test`, shows reachable/latency
- "Delete" button with confirmation dialog
- OAuth success/error banners when redirected from OAuth callback
- OAuth callback now redirects to `/dashboard/credentials` instead of non-existent `/credentials`

### API Type Safety

- Proxy endpoint: `body: dict` → `body: ProxyRequest` (Pydantic model)
  - `ProxyRequest(resolve_token: str, method: Literal[...], url: str, json_body: dict | None)`
  - Invalid payloads now get HTTP 422 with validation details
- Resolve endpoint: response typed with `ResolveCredentialResponse`
- Proxy response typed with `ProxyResponse(status_code: int, body: Any)`
- Router docstring updated from "skeleton" to accurate description

### Adapter Refresh

- `adapter-langchain`: `langchain-core>=0.2` → `>=0.3`
- `adapter-crewai`: `crewai>=0.80.0` → `>=0.86.0`
- No API changes in adapter code — compatibility-only bumps

### Ops Requirements

**Redis is now a hard production dependency for OAuth flows.** Previously, OAuth
used an in-memory dict that lost state on restart and didn't work across multiple
instances. If your production deployment uses OAuth connectors, ensure Redis is
reachable.

### Upgrade Notes

- **Backend**: No schema migrations. New Redis key pattern `oauth:state:*` (auto-expires).
- **SDK**: New `cleanup_old_runs()` function. `run_log` section in config.json is optional.
- **CLI**: Two new commands (`runs`, `credentials`). No changes to existing commands.
- **Frontend**: New route `/dashboard/credentials`. No changes to existing pages.
- **Adapters**: Version bounds bumped — install may pull newer transitive deps.

---

## v0.4.0 — Hardening & Production-Readiness (2026-04-14)

v0.4 makes the existing architecture production-ready. No new execution models,
no new package types. The guiding principle: finish what exists.

### What's New

**Agent Run Observability**
- Every agent execution gets a UUID4 `run_id`
- Structured JSONL run logs at `~/.agentnode/runs/{run_id}.jsonl`
- Events: `run_start`, `tool_call`, `tool_result`, `iteration`, `step_start`, `step_result`, `run_end`
- Audit records now correlate via `run_id` (previously `request_id=None`)
- Hard limit: 1000 entries per run, then a final `truncated` event
- `RunToolResult` now carries `run_id` for traceability

**Credential Health Check**
- `POST /v1/credentials/{id}/test` now performs real connectivity tests
- Returns `reachable`, `status_code`, `latency_ms`, `message`
- Uses the connector's `health_check.endpoint` from the manifest

**OAuth2 PKCE Flow**
- `POST /v1/credentials/oauth/initiate` — starts authorization flow
- `GET /v1/credentials/oauth/callback` — handles code exchange
- PKCE with S256 code challenge (no plain)
- Supported providers: GitHub, Slack (explicit, not pluggable)
- In-memory state store with 5-minute TTL

**Process-based Agent Isolation**
- Agents can run in a `multiprocessing.Process` with terminate→kill escalation
- Configurable via `agent.isolation` in the manifest: `"process"` or `"thread"`
- **Default: `"thread"`** — this is a conscious decision, not a gap (see below)
- Grace period between SIGTERM and SIGKILL

**Vault-SDK Bridge**
- SDK credential resolution chain: env var → local file → API → None (local file added in v0.5.1)
- `GET /v1/credentials/resolve/{provider}` returns a short-lived JWT (60s TTL)
- `POST /v1/credentials/proxy` executes requests server-side with injected credentials
- Secrets never leave the server in plaintext
- Configurable: `credentials.resolve_mode` in `~/.agentnode/config.json` (`"env"`, `"local"`, `"api"`, `"auto"`)

**Resource Content Delivery**
- `resource://` URIs can now serve inline content from installed package files
- Looks for files in `resources/{name}.{json,txt,md,yaml,yml,csv,xml}`
- Hard limit: 100KB per resource
- `https://` remains `uri_reference` only (no implicit fetching)
- MCP adapter updated: inline content for `resource://`, metadata fallback if no file

**Conditional Orchestration Steps**
- Sequential steps can have a `when` expression
- Supported syntax: `$ref == value`, `$ref != value`, `$ref is null`, `$ref is not null`
- Unresolvable `$ref` → step is skipped (not an error)
- Skipped steps tracked with `skipped: true` in `step_details`
- No `and`/`or` — one condition per step

### Important Defaults

**`agent.isolation` defaults to `"thread"`, not `"process"`.**

This is intentional. Windows spawn semantics require picklable entrypoints,
and `AgentContext` contains lazy imports and closures (`run_tool`) that are
not trivially serializable across process boundaries. Thread isolation with
daemon thread + timeout + audit is sufficient for current use cases.
Manifest authors who know their entrypoint is pickle-safe can opt into
`agent.isolation: "process"`.

### What's Unchanged

- `package_type`: still only `toolpack`, `agent`, `upgrade`
- All v0.3 security boundaries (S1–S12) hold
- No auto-injection for prompts/resources (discover/select/read)
- Agent-v1: strict allowlist, no delegation, no dynamic tool discovery
- `install_mode`: still `package` or `remote_endpoint` only

### What v0.4 Intentionally Does NOT Include

| Feature | Reason |
|---------|--------|
| Parallel orchestration | No user demand, debugging complexity |
| Planning orchestration | Too early — sequential + conditions covers real cases |
| Token budgets | No token reporting infrastructure |
| Agent-to-agent delegation | Trust boundary questions unresolved |
| Persistent agent state | No state management layer |
| `auth_type=custom` | Too open, security risk |
| Generic OAuth provider registry | Explicit per-provider until 5+ exist |
| Implicit HTTPS resource fetching | `resource://` = inline, `https://` = reference |
| New package types | Architecture discipline from v0.3 |

### Upgrade Notes

- **SDK**: `RunToolResult` has a new optional `run_id` field (default `None`). Non-breaking.
- **Manifests**: New optional fields `agent.isolation`, `resource.content_path`, `step.when`. All backward-compatible.
- **Backend**: New endpoints under `/v1/credentials/` (OAuth, resolve, proxy). Existing endpoints unchanged.
- **Run logs**: Written to `~/.agentnode/runs/`. Automatic cleanup added in v0.5.

---

## v0.3.0 — Taxonomy & Agent Runtime (2026-04-14)

8-PR series establishing the capability taxonomy, agent runtime, and sequential orchestration.

See commit history for details.

---

## v0.2.0 — Package Format & Core Platform (2026-Q1)

Initial release with ANP v0.2 format, multi-tool entrypoints, trust levels,
search, resolution, and CLI.
