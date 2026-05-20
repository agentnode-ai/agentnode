# Changelog

## 0.6.2 — Connector/Remote Runtime Hardening

Closes the enforcement gap between Guard and the HTTP boundary for
remote/connector tools. Credentials are now domain-bound and
HTTPS-only before any request leaves the process.

### Added

- **HTTPS-only credential enforcement** — `CredentialHandle` refuses to
  send credentials over non-HTTPS URLs. `_require_secure_target()` runs
  before every `authorized_request()` and `authorized_request_headers()`
  call. Covers `http://`, empty-scheme, and relative URLs.
- **Empty-domain binding denial** — `CredentialHandle` with empty
  `allowed_domains` now raises `PermissionError` instead of allowing any
  host. Closes the open-proxy gap (GAP-1).
- **Method/action-type consistency warnings** — remote runner detects
  mismatches between HTTP method and declared `action_type` (e.g.
  `action_type=read` with `POST`). Advisory only — logged and audited,
  never blocks. Guard remains the policy authority.
- **Request/response size warnings** — remote runner measures JSON
  request payload and response body size. Warns on requests >10 MB or
  responses >50 MB. Never blocks. Audit includes size fields only when
  thresholds are exceeded.
- **Scope/method mismatch logging** — remote runner detects mutating
  HTTP methods (POST/PUT/PATCH/DELETE) when all declared connector
  scopes appear read-only. Heuristic-based, advisory only.
- **Remote audit fields** — `_audit_remote_call()` now records
  `remote_method`, `remote_domain`, `remote_status_code`,
  `remote_duration_ms`, `remote_provider`, and conditional warning
  fields. All fields use `remote_` prefix. No URLs, paths, kwargs,
  bodies, or secrets in audit entries.
- **Guard config cache invalidation** — guard config is reloaded when
  the config file's mtime or size changes, without restarting the
  process.
- 105 new tests across all phases (characterization, enforcement, audit).

### Fixed

- **Word-counter E2E argument shape** — test helper passed arguments in
  wrong format.

### Security

- Deny happens before `httpx.request()` — credentials never reach the
  wire for denied requests.
- Empty `allowed_domains` is a hard deny, not a permissive default.
- Remote runner advisory checks never override Guard decisions.
- Audit entries contain only safe metadata (hostname, status code,
  duration, method). No full URLs, request bodies, or credentials.

## 0.6.1 — Runtime Audit Parity & Input Guard Escalation

Audit completeness and input validation hardening after the v0.6.0
architecture review.

### Added

- **`runtime_run` audit event** — unified dispatch-level audit for all
  runtimes (python, mcp, remote, agent). Emitted after every tool
  execution with runtime type, success/failure, and error summary.
  No kwargs or result data in audit entries.
- **`mcp_run` audit event** — execution-result audit for MCP tool calls,
  analogous to `remote_run`. Records duration, success/failure, and
  error class. Emitted only for actual tool execution, not pre-execution
  guard decisions.
- **Input guard escalation** — `path_traversal` and `url_anomaly`
  findings promoted from warning to blocking:
  - Interactive mode: returns `policy_prompt` (requires confirmation)
  - Non-interactive mode: returns `policy_denied` (fail-closed)
  - Warning-level findings (oversized inputs) remain non-blocking
  - Guard/policy decisions take precedence — input guard never overrides
    an earlier deny
- **`InputFinding` dataclass** — structured findings with `level`
  (warning/prompt), `message`, and `code` fields. `str()` returns the
  message for backward compatibility with `policy_info["input_warnings"]`.
- 50 new tests covering all three sub-phases.

### Fixed

- **Missing `import os` in CLI commands** — `agentnode run --explain`
  and `--json` crashed with `NameError`. Pre-existing since Phase 5.
- **`UnboundLocalError` in MCP runner** — `name` variable was assigned
  inside try block but referenced in except handler. Fixed by
  initializing before the try.

### Security

- Input guard fail-closed in non-interactive mode: if a finding is
  severe enough to require human confirmation, it must not silently
  pass when no human is present.
- Audit entries never contain tool arguments, result data, or secrets.

## 0.6.0 — Guard: Pre-Execution Policy Gateway

Runtime guardrails for AI agent tool calls. Guard sits between the policy
check and tool execution, classifying every tool invocation by action type
and applying configurable policy — before any code runs.

### Added

- **AgentNode Guard** — pre-execution policy gateway with 9 action types:
  `read`, `compute`, `write_local`, `write_external`, `delete`, `execute`,
  `credential_use`, `network_egress`, `unknown`. Each action type maps to
  a policy decision: `allow`, `prompt`, or `deny`.
- **Default policy** — safe defaults out of the box: read/compute/write_local/
  network_egress are allowed; delete/write_external/execute/credential_use
  require confirmation; unknown requires confirmation.
- **Strict mode** — `AGENTNODE_GUARD_STRICT=true` escalates: delete/
  write_external/execute/unknown become hard `deny`, write_local becomes
  `prompt`. Designed for production and CI.
- **Per-tool policy overrides** — `agentnode guard set <action> <decision>
  --tool <slug/tool>` overrides global policy for a specific tool. Overrides
  never bypass critical risk, strict mode, or install/run denial.
- **Guard CLI** —
  - `agentnode guard status` — show resolved policy, strict mode, rate limits
  - `agentnode guard set <action> <decision>` — set global or per-tool policy
  - `agentnode guard unset <action>` — reset to default
  - `agentnode guard check <slug/tool> [--action <type>] [--json]` — dry-run
    policy check without executing
  - `agentnode guard reset` — reset all policies to defaults
- **Action classification** — three-tier: manifest declaration (highest),
  name heuristic (fallback), permission signals (escalation). Permission
  signals add `network_egress`, `execute`, or `credential_use` on top of
  the declared/inferred type.
- **Risk scoring** — composite score from action types, trust level, and
  environment secrets. Levels: low (0–20), medium (21–45), high (46–70),
  critical (71+). Critical risk is always denied (unoverridable).
- **Connector credential bypass** — connectors with declared `auth_type`
  get `credential_use: allow` without prompting when the global policy
  allows it.
- **Agent pre-approved actions** — agent packages declare
  `pre_approved_actions` in their manifest. Pre-approved action types skip
  the prompt. High-risk actions not in the list are denied in
  non-interactive mode, prompted in interactive mode.
- **MCP argument inspection** — deep inspection of tool arguments for path
  traversal, absolute path escape, URL anomalies, shell tokens, oversized
  payloads, excessive nesting, and excessive keys. Schema-aware: suppresses
  false positives when `input_schema` declares free-text or URL fields.
- **Rate limiting** — per-slug sliding window with burst/minute/hour limits.
  Defaults: 60/min, 1000/hour, burst 10. Agents get 120/min, 2000/hour,
  burst 20. Strict mode halves the defaults.
- **Install-time risk preview** — `agentnode install` shows a guard risk
  preview (action types, risk level, policy decisions) before installing.
- **Guard chain tracing** — each policy step recorded as
  `guard_action:{decision}({action_type}[:context])`. Chain visible in
  `guard check --json` and audit entries.
- **CLI confirmation UX** — interactive confirmation prompt with risk
  coloring, mitigation hints, and fail-closed default (No = tool does not
  run).
- **Audit integration** — guard decisions logged to `audit.jsonl` with
  `guard_check` event type. `agentnode audit --type guard_check` filters
  guard events.
- **Skill system** —
  - `agentnode skill install <slug>` — install skill packages
  - `agentnode skill list` — list installed skills
  - `agentnode skill show <slug> [--raw] [--render <args>]` — display skill
    prompts with placeholder rendering
  - MCP server exposing skill prompts and assets
  - Skills bypass guard (no tool execution)
- **`agentnode publish`** — publish packages to the AgentNode registry with
  artifact upload, manifest validation, and pre-publish confirmation gate.
- **Token connector auth** — `auth_type: "token"` supported in connector
  manifests alongside OAuth2.

### Changed

- **Audit display** — `agentnode audit` supports `--type` filter and
  improved formatting for guard events.
- **JSON response guard** — both sync and async SDK clients now validate
  that success-path JSON responses are dicts. Arrays, strings, numbers,
  and null trigger `AgentNodeError` instead of silent pass-through.

### Security

- **OC-1**: Guard imports no runtime-specific modules (python_runner,
  mcp_runner, etc.).
- **OC-2**: Decision path is pure in-memory — no file I/O, no network.
- **OC-3**: Internal exceptions always fail closed — never allow on error.
- **Critical risk is unoverridable** — unverified packages with high-risk
  actions in environments with secrets are always denied, regardless of
  policy configuration.
- **Strict mode tool override bypass prevented** — per-tool overrides are
  ignored in strict mode.
- **Publish confirmation gate** — `agentnode publish` requires explicit
  `y` confirmation (or `--yes`) before uploading.
- **Production startup guard** — backend blocks startup when
  `ENVIRONMENT=production` and default secrets are still configured.

### Fixed

- **Mitigation hint accuracy** — guard prompt hints now reference the actual
  blocking action type instead of the first alphabetical action type.

### Design Constraints

- Guard is a decision layer, not a sandbox. It classifies and gates; it does
  not isolate execution.
- Policy resolution is deterministic: tool_override > global action_policy >
  default. Strict mode replaces the effective policy layer.
- All guard state is in-memory. No guard decision depends on file I/O or
  network calls.
- Agent `pre_approved_actions` come from the manifest and config overrides.
  They are not inherited or guessed.

### Migration

- No breaking changes. All new features are additive.
- Default guard policy matches pre-0.6.0 behavior: read/compute/write_local/
  network_egress allowed, everything else prompted.
- To enable strict mode: `export AGENTNODE_GUARD_STRICT=true`
- To customize policy: `agentnode guard set <action_type> <decision>`
- To add per-tool overrides: `agentnode guard set <action_type> <decision>
  --tool <slug/tool_name>`

## 0.5.3 — Configurable Risk Policies

User-configurable policies for computed risk flags. Extends the risk
profile from Phase 9 with actionable reactions — without changing the
default behavior.

### Added

- **`risk_policies` config section** — per-flag policy configuration
  using the same `allow | log | prompt | deny` values as permissions.
  Default: `external_write_capable: log` (audit only, no blocking).
- **`check_risk_policies()`** — internal policy check that evaluates
  risk flags after `check_run()`. Only fires when the hard policy
  already allowed execution. Hard policy always has priority.
- **Runner integration** — `run_tool()` now evaluates risk policies
  between the permission check and execution. Prompt/deny messages
  clearly identify the risk policy as the source.

### Usage

```bash
# View current setting
agentnode config get risk_policies.external_write_capable

# Require confirmation for external-write-capable packages
agentnode config set risk_policies.external_write_capable prompt

# Block external-write-capable packages
agentnode config set risk_policies.external_write_capable deny

# Reset to audit-only (default)
agentnode config set risk_policies.external_write_capable log
```

### Design

- Default is `log` — nothing is blocked out of the box.
- Risk policies react to **computed** risk flags (from `risk_profile.py`),
  not declared permissions. `permissions.*` handles declared permissions.
- Risk policies only apply to `run_tool()`, not install.
- When a risk policy blocks, the error message says so:
  `"Blocked by risk policy: external_write_capable is configured as deny."`

## 0.5.2 — Usage Risk Profile

Per-package usage risk scoring — separate from the verification score.
Risk answers "how risky is the usage?" not "does it work reliably?"

### Added

- **Usage Risk Profile** — `compute_risk_profile()` scores packages from
  static signals (permissions, trust, credentials) and runtime signals
  (audit deny rate). Score 0–100, level low/medium/high.
- **`get_risk_profile(slug)`** — public API to retrieve the risk profile
  for any installed package. Returns `None` if not installed.
- **Risk flags** — semantic boolean flags like `external_write_capable`
  that categorize risk without affecting the numeric score. Derived from
  network permissions, connectors, and capability IDs.
- **Inspect integration** — `agentnode inspect` now shows Usage Risk
  section (level, score, signals, flags) in both CLI and `--json` output.
- **Backend hint** — optional `risk_score`/`risk_profile` from backend
  metadata is displayed separately but never included in the local score.
- Exports: `RiskProfile`, `compute_risk_profile`, `get_risk_profile`
  available from `agentnode_sdk`.

## 0.5.1 — Security Visibility & Guardrails

Hardening release. Adds visibility into tool inputs, plan-level data flows,
LLM-facing tool outputs, and agent auto-install behavior. All new checks are
informational warnings — no blocking rules that could break existing packs.

### Added

- **`agentnode inspect <slug>`** — security-focused report for installed
  packages: trust level, permissions, runtime, tools, connector info, and
  audit history summary. Supports `--json`.
- **Input guard** — `validate_tool_input()` warns on path traversal patterns,
  oversized strings (>1 MB), oversized collections (>10k items), and URL
  arguments when the package declares `network_level=none`. Warnings appear
  in `RunToolResult.policy["input_warnings"]`.
- **Plan-level risk warnings** — `check_plan_risk()` flags risky step
  combinations: filesystem-read followed by network access, code execution
  followed by network access, and >2 network steps. Warnings shown in CLI
  before execution. `audit_plan()` logs the full plan as a single audit entry.
- **LLM tool output marking** — `mark_untrusted_tool_output()` truncates tool
  results >50 KB before passing to the LLM and wraps content containing
  prompt injection markers in structured delimiters. Injection detection
  triggers a run log event.
- **Agent auto-install guard** — `AgentContext._ensure_installed()` now
  respects `auto_upgrade_policy` from config. When set to `off`, agent
  auto-install is blocked and logged.
- **Shared audit reader** — `read_audit_entries()` extracted to `cli/audit.py`
  as the single entry point for reading `audit.jsonl`. Both `cmd_audit()` and
  `cmd_inspect()` use it.

### Security

- All new checks are warning-only. No existing packs or workflows are blocked.
- Input guard warnings are logged and included in `RunToolResult.policy`.
- Plan risk warnings are informational — shown but never block execution.
- LLM output marking does not claim to prevent prompt injection; it marks
  untrusted data and detects common injection patterns.
- Agent auto-install guard is a policy gate, not a security boundary — it
  respects the user's existing `auto_upgrade_policy` setting.

## 0.5.0 — Intelligence, Planner & Hardening

### Breaking changes

- None.

### Added

- **Multi-step planner** — `agentnode run "extract from report.pdf then
  translate to german"` decomposes tasks via connectors (`then`,
  `and then`, `→`, `after that`, `afterwards`), pipes output between
  steps — so users no longer need manual copy-paste between commands —
  and executes each step via `run_tool()` with full policy/audit.
  Max 3 steps. Available as CLI and Python API (`plan_task()`,
  `plan_and_run()`).
- **Capability graph** — typed weighted edges (`complements`, `requires`,
  `enhances`) between 27 capabilities. Powers gap detection, recommendations,
  and re-ranking. `requires` is used sparingly (only `vector_memory →
  embedding_generation`).
- **Capability taxonomy** — separates runtime capabilities (`active`, have
  installable packages) from authoring capabilities (`planned`, no packages
  yet). `missing_for()` never suggests planned capabilities. Helpers:
  `is_runtime_capability()`, `is_known_capability()`, `list_capabilities()`.
- **`agentnode auth`** — credential management CLI (`set`, `list`, `remove`,
  `status`). Credentials stored with 0600 permissions via atomic writes.
- **`agentnode audit`** — shows recent policy decisions from the append-only
  `audit.jsonl` trail. Supports `--json` for structured output.
- **`agentnode logs`** — shows agent run logs. Supports per-run detail view
  and `--json` output.
- **`agentnode config list`** — shows all settings with descriptions and
  allowed values.
- **`--json` output** on `run`, `resolve`, `doctor`, `recommend`, `audit`,
  `logs` commands.
- **`--explain` on `run`** — shows capability detection, confidence, matched
  package, alternatives, and policy decision for both slug and smart runs.
- **`--dry-run` on `run`** — shows the execution plan (single or multi-step)
  without executing.
- **Synonym matching** — 40+ natural language synonyms for capabilities
  (e.g. "take screenshot" → `screenshot_capture`). Longest phrase matches
  first to prevent partial match ambiguity.
- **Client-side re-ranking** — `rerank()` boosts packages that complement
  installed capabilities, penalizes overlap and already-installed packages.
  Boost clamped to +10/−20 to not oversteer server scores.
- **`RunToolResult.to_dict()`** — structured serialization with policy info
  for `--json` output.
- **`RunToolResult.policy`** — every execution result now carries the policy
  decision (`action`, `reason`, `source`).
- **Run log events** — `step_start`, `step_result`, `llm_call` events for
  agent observability. Retention with configurable `max_age_days` and
  `max_count`.

### Changed

- **`agentnode doctor`** — now uses capability graph for prioritized gap
  detection with accumulated scores and human-readable reasons. Supports
  `--json`.
- **`agentnode recommend`** — rewritten with priority levels (`high`,
  `suggested`, `low`), reason strings, and `--json` structured output.
- **`agentnode resolve`** — re-ranks results using local context (installed
  capabilities and packages). Supports `--json`.
- **Config cleanup** — removed deprecated `allow_unverified` setting. Config
  values validated against allowed sets.
- **Smart run** respects `auto_upgrade_policy` and `install_confirmation`
  from user config. `doctor --fix` respects the same policies.
- **Backward-compatible complements** — `CAPABILITY_COMPLEMENTS` dict now
  derived from the capability graph, not maintained separately.
- **Multi-step CLI guardrails** — `install_confirmation: prompt` respected
  before auto-installing in multi-step mode. Low-confidence steps require
  interactive confirmation or abort in non-interactive mode.

### Security

- **Trust TTL refresh** — `run_tool()` re-checks trust level from backend
  every 7 days. Network failure falls back to cached trust (fail-open on
  read, never on write).
- **`load_tool()` RuntimeWarning** — warns that `load_tool()` bypasses
  policy checks, directing callers to `run_tool()`.
- **Non-interactive mode** — `AGENTNODE_NON_INTERACTIVE=true` disables
  interactive prompts. Policy decisions that require approval are denied
  instead of blocking.
- **Atomic writes** — config, lockfile, and credential store use
  `tempfile` + `os.replace()` to prevent corruption on crash.
- **File locking** — lockfile updates use cross-platform advisory locks
  (`fcntl` on Unix, `msvcrt` on Windows) with sidecar `.lk` files.
- **TOCTOU fix in `remove`** — confirmation prompt runs outside the file
  lock, then re-reads inside the lock before modifying.
- **Credential store** — uses `atomic_write_json()` with `mode=0o600`.
- **Safe piping** — multi-step planner extracts specific keys (`text`,
  `content`, `result`) from previous step output instead of blind `**kwargs`.
- **Install policy in planner** — auto-install uses the standard
  `client.install()` route, respecting `auto_upgrade_policy`,
  `minimum_trust_level`, and `install_confirmation`.

### Fixed

- **Lockfile deduplication** — duplicate package entries no longer
  accumulate across installs.
- **Dead code removal** — removed unused `_policy_check_install` mock from
  conftest, stale imports, unreachable code paths.
- **v0.2 `load_tool` fallback** — no longer attempts entrypoint fallback
  when a tools list is present, preventing false import errors.
- **`_cmd_run_smart` install flow** — no longer silently skips install when
  `auto_upgrade_policy: off`. Shows clear message with manual install
  command.

### Known limitations

- **Planner: max 3 steps** — hard MVP limit, no workaround.
- **Planner: literal connectors only** — splits on "then", "and then", "→",
  "after that", "afterwards". No comma, semicolon, or LLM-based
  decomposition.
- **Piping is heuristic** — extracts `text`/`content`/`result` keys from
  dict outputs. Tools with non-standard output keys get the whole dict
  wrapped as `{"input": dict}`.
- **`_has_explicit_input` knows limited modifiers** — only `target_language`
  is recognized as a modifier key. Additional modifiers must be added
  manually.
- **Taxonomy `active` status is manual** — maintained in
  `capability_taxonomy.py` until a registry-backed capability index exists.
- **No `install_confirmation: prompt`** in core API `plan_and_run()` — the
  API is non-interactive by design. The CLI layer handles prompting.

## 0.4.1 — Security & Correctness

**Behavioral change:** `run_tool(mode="auto")` now always executes via
subprocess isolation, regardless of trust level. This makes the
documented isolation guarantee true by default. `mode="direct"` remains
available as an explicit opt-in for performance-critical workloads that
knowingly share in-process globals.

**Migration note:** Tools that rely on shared in-process state
(module-level globals, process-wide singletons) should explicitly pass
`mode="direct"` going forward.

### Fixes

- **AsyncAgentNode /v1 base URL** — the async client now appends `/v1` to
  `base_url` when missing, matching `AgentNode` (sync). Previously all
  `AsyncAgentNode` calls hit `/packages/...` and 404ed against
  production. (P0-04)
- **AgentNodeClient.install()** now POSTs
  `POST /v1/packages/{slug}/install` so the backend tracks the install
  event. Previously installs went untracked. (P0-05)
- **run_tool(mode="auto") always uses subprocess** — see behavioral
  change above. (P0-06)
- **Response parsing hardening** — `_handle`/`_request` no longer crash
  on non-dict JSON error bodies or HTML/plain-text 2xx responses; both
  are now surfaced as `AgentNodeError`. (P1-SDK3, P1-SDK4)
- **run_tool reserved kwargs** — passing the internal `entry` kwarg via
  `**kwargs` now raises `TypeError` instead of silently shadowing the
  dispatcher's forwarding path. (P1-SDK5)
- **Installer download ceiling** — `download_artifact` now enforces a
  500 MB hard ceiling (`MAX_DOWNLOAD_BYTES`). Declared
  `Content-Length` is checked up front; streamed bytes are checked per
  chunk. Oversized downloads are aborted and the partial file removed.
  (P1-SDK6)
- **run_tool dispatch logging** — `runner.run_tool` now emits an `INFO`
  log line with the resolved runtime and mode, so callers can confirm
  what mode `auto` actually picked without inspecting the
  `RunToolResult` after the fact. (P1-SDK10)
