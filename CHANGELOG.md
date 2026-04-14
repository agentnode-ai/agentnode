# Changelog

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
- SDK credential resolution chain: env var → API → None
- `GET /v1/credentials/resolve/{provider}` returns a short-lived JWT (60s TTL)
- `POST /v1/credentials/proxy` executes requests server-side with injected credentials
- Secrets never leave the server in plaintext
- Configurable: `credentials.resolve_mode` in `~/.agentnode/config.json` (`"env"`, `"api"`, `"auto"`)

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
- **Run logs**: Written to `~/.agentnode/runs/`. No cleanup mechanism yet — manage manually if needed.

---

## v0.3.0 — Taxonomy & Agent Runtime (2026-04-14)

8-PR series establishing the capability taxonomy, agent runtime, and sequential orchestration.

See commit history for details.

---

## v0.2.0 — Package Format & Core Platform (2026-Q1)

Initial release with ANP v0.2 format, multi-tool entrypoints, trust levels,
search, resolution, and CLI.
