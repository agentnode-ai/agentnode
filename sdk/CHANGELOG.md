# Changelog

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
