# AgentNode SDK — Threat Model

Last updated: 2026-05-22

## Scope

This document covers the AgentNode SDK's local execution model — the code that runs on a developer's machine when they install and run agent tools. It does **not** cover the AgentNode registry backend or web application.

## What AgentNode enforces

| Protection | How | Where |
|---|---|---|
| **Pre-execution policy gate** | `check_run()` evaluates trust level, permissions, and environment before every tool execution. Returns allow/deny/prompt. | `policy.py` |
| **Guard: action-type policy** | `check_action()` classifies every tool call into action types and applies configurable allow/prompt/deny policy. Runs after `check_run()`, before tool dispatch. | `guard.py` |
| **Guard: critical risk denial** | Unverified packages + high-risk actions + environment secrets → risk score ≥71 → hard deny. Cannot be overridden by policy or tool overrides. | `guard.py` |
| **Guard: MCP argument inspection** | Deep inspection of MCP tool arguments: path traversal, absolute paths, URL anomalies, shell tokens, oversized payloads, excessive nesting. Schema-aware false-positive suppression. | `guard.py` |
| **Guard: rate limiting** | Per-slug sliding window rate limits (burst/minute/hour). Prevents runaway tool loops. | `guard.py` |
| **Guard: strict mode** | `AGENTNODE_GUARD_STRICT=true` escalates delete/write_external/execute/unknown to deny. Per-tool overrides are ignored. | `guard.py` |
| **Guard: fail-closed (OC-3)** | Any internal exception in the guard path → deny (strict) or prompt (default). Never allow on error. | `guard.py` |
| **Environment variable filtering** | Subprocess mode runs tools with a stripped environment. Only `PATH`, `HOME`, `PYTHON*`, `TEMP`, and `AGENTNODE_LOCKFILE` are passed. API keys (`AWS_*`, `OPENAI_*`, `STRIPE_*`, etc.) are excluded. | `python_runner.py` |
| **Subprocess timeout** | Tools in subprocess mode are killed after a configurable timeout (default 30s). | `python_runner.py` |
| **Trust level minimum** | Users set a minimum trust level (default: `verified`). Packages below this threshold are denied. | `policy.py` |
| **Non-interactive deny** | In CI/non-interactive environments, any `prompt` decision escalates to `deny`. | `policy.py` |
| **Fail-closed on broken config** | If `config.json` is missing or invalid, policy defaults to deny (non-interactive) or prompt (interactive). | `policy.py` |
| **Trust refresh** | Trust levels are re-fetched from the registry every 7 days. | `runner.py` |
| **Audit trail** | All policy decisions are logged to `~/.agentnode/audit.jsonl`. Append-only, rotated, local-only. No secrets logged. | `policy.py`, `guard.py` |
| **MCP env filtering** | MCP server subprocesses use the same allowlist-based environment filtering. | `mcp_runner.py` |
| **Credential domain lock** | `CredentialHandle` validates the target domain against `allowed_domains` before attaching credentials. Empty `allowed_domains` is a hard deny (no open-proxy default). Secrets are not exposed via properties. | `credential_handle.py` |
| **Credential HTTPS enforcement** | `_require_secure_target()` denies credentialed requests over `http://`, empty-scheme, or relative URLs. Runs before every `authorized_request()` call — credentials never reach the wire for denied requests. | `credential_handle.py` |
| **Remote method/action-type warnings** | Remote runner detects mismatches between HTTP method and declared `action_type` (e.g. read + POST). Advisory only — logged and audited, never blocks. Guard remains the policy authority. | `remote_runner.py` |
| **Remote payload size warnings** | Request >10 MB and response >50 MB trigger warnings in logs and audit. Never blocks execution. | `remote_runner.py` |
| **Remote scope/method warnings** | Mutating HTTP methods with all-read-only scopes trigger advisory warnings. Heuristic-based, never blocks. | `remote_runner.py` |
| **Remote audit trail** | Every remote call audits `remote_method`, `remote_domain`, `remote_status_code`, `remote_duration_ms`, `remote_provider`, and conditional warning fields. No full URLs, request bodies, or credentials. | `remote_runner.py` |
| **Guard config hot-reload** | Guard config is reloaded when the config file's mtime or size changes. No restart required. | `guard.py` |
| **Agent tool allowlist** | Agent packages can only call tools explicitly listed in their manifest. | `agent_runner.py` |
| **Lockfile entry integrity** | Per-entry SHA-256 hash over canonical fields (entrypoint, runtime, remote_endpoint, mcp_command, permissions, tools, connector, agent, etc.). Detects post-install mutation of lockfile entries. Default mode: warn + audit. Strict mode: deny before execution. | `lock_integrity.py`, `runner.py` |
| **Lockfile integrity CLI** | `agentnode lock seal` computes hashes, `agentnode lock verify` checks them. Exit code 1 on mismatch. `--strict` treats missing integrity as failure. Designed for CI pipelines. | `cli/commands.py` |
| **Install-time sealing** | New installs and upgrades automatically include `_integrity` hash. No manual seal step required for new packages. | `installer.py` |
| **Publisher signature verification** | Ed25519 signatures verified on install before lockfile write. Invalid signature → install blocked (no override). Missing signature → warn (gradual adoption). Verification uses cached public key only (no registry call). | `signature.py`, `installer.py` |
| **Publish-time signing** | `agentnode publish` signs the canonical payload (slug + all canonical fields) with the publisher's Ed25519 private key. Signing failure warns but does not block publishing. | `cli/publish.py`, `signing_key.py` |
| **Signature integrity (canonical v2)** | `_integrity` v2 hash covers `_signatures`. Swapping the signature + public key in a lockfile entry invalidates the integrity hash. v1 entries without signatures continue to verify against v1 field list. | `lock_integrity.py` |
| **Signature status in CLI** | `agentnode lock verify` reports signature status per package with exit code 1 on invalid/unknown_key. `agentnode inspect` shows signature details. Both use cached public key — no registry dependency. | `cli/commands.py` |
| **Publisher identity integrity** | Entry-Level `publisher_slug` is a canonical field (v3). Post-install manipulation causes integrity mismatch. Displayed publisher identity is integrity-protected offline. Write-once at install time — never overwritten by trust refresh or registry sync. | `lock_integrity.py` |
| **Online key verification** | `lock verify --online` checks each signed package's key_id against the registry. Revoked/unknown/mismatched keys cause exit code 1. Registry unreachable also causes exit 1 (fail-closed for CI). | `key_status.py`, `cli/commands.py` |
| **Install-time revocation** | Install blocks packages with revoked publisher keys when the registry reports key status in the install response. No additional network call — uses data already present in the detail API response. | `installer.py` |

## What AgentNode does NOT enforce

| Threat | Current state | User mitigation |
|---|---|---|
| **Network access by tools** | Declared in manifest, checked by policy gate, but **not restricted at runtime**. A tool declaring `network: none` can still make HTTP requests. | Review permissions before installing. Use `agentnode inspect` to see declared permissions. |
| **Filesystem access by tools** | Same as network — declared, policy-checked, but not sandboxed. | Review permissions. Run in a VM or container for sensitive workloads. |
| **Connector scope enforcement** | Connector scopes are declared but not enforced at runtime. A tool with `read`-only scopes can still issue mutating HTTP methods. The remote runner warns on obvious mismatches (advisory only). | Review connector scopes in manifest. Remote runner warnings appear in logs and audit. |
| **Direct mode env access** | `mode="direct"` runs tool code in the same process with full environment access, including API keys. | Use `mode="auto"` (default), which always resolves to subprocess. |
| **`load_tool()` bypass** | Calling `load_tool()` directly skips all policy checks and audit logging. | Use `run_tool()` instead, which goes through the full policy pipeline. |
| **Malicious package code** | Trust level and verification reduce risk but do not prevent a determined attacker. | Only install packages from trusted publishers. Review source code for sensitive use cases. |
| **Inter-tool data leakage** | Tools in the same subprocess session share the filtered environment. | No current mitigation. |
| **Lockfile entry addition** | Integrity is per-entry, not global. Adding a new malicious entry is not detected. | Review lockfile diffs in PRs. Global lockfile hash planned for a future phase. |
| **`trust_level` manipulation** | `trust_level` is mutable (TTL refresh updates it). Local manipulation from `unverified` to `trusted` is not detected by integrity checks. | Trust enforcement relies on policy/TTL mechanisms, not lockfile integrity. |
| **Key revocation (runtime)** | `lock verify --online` detects revoked keys. Install blocks revoked keys when the registry reports status. Runtime does not check revocation — uses offline signature only. | Run `lock verify --online` in CI. |
| **Registry response signing** | The registry response itself is not signed. A compromised registry could omit signatures or serve malicious metadata. Publisher signatures protect against artifact replacement but not metadata-only attacks. | Registry signing key infrastructure planned. |

## Privacy boundary

- **Local execution**: All tool execution happens on the user's machine. No tool inputs, outputs, or logs are sent to AgentNode.
- **What the registry sees**: Install events, search queries, trust-level refresh requests.
- **Audit logs**: Stored at `~/.agentnode/audit.jsonl`, never transmitted. Contains only policy decisions (action, source, reason, trust level), never tool arguments or results.

## Architecture summary

```
User calls install_package()
  → Download artifact, verify SHA-256 hash
  → Build lock_entry from downloaded artifact (not registry metadata)
  → Verify publisher signature: verify_entry_signature()
      → Valid: log info, continue
      → Missing: warn, continue (gradual adoption)
      → Invalid/malformed/wrong-key: RuntimeError — install blocked
  → seal_entry() (canonical_version v2 if signed, v1 if unsigned)
  → update_lockfile() — only reached after signature verification

User calls run_tool()
  → Lockfile integrity: verify_entry() — warn or deny on mismatch
  → Policy kernel: check_run() — allow / deny / prompt
  → Risk policies: check_risk_policies() — flag-based reactions
  → Input guard: check_inputs() — warnings only, never blocks
  → Guard: check_action() — action-type policy gate
      → classify action types (manifest > name heuristic > permissions)
      → compute risk score (action types + trust + secrets)
      → resolve policy (tool override > global > default; strict replaces all)
      → critical risk → hard deny (unoverridable)
      → rate limit check (burst / minute / hour)
      → MCP: inspect_mcp_args() — deep argument inspection
  → Runtime dispatch:
      → Subprocess (default): filtered env, timeout, tmpdir
      → Direct: in-process, full env (explicit opt-in only)
      → MCP: subprocess with filtered env, JSON-RPC over stdio
      → Remote: HTTPS-only via CredentialHandle, domain-locked,
               method/size/scope advisory checks, per-call audit
      → Agent: orchestrator with tool allowlist, iteration limits
```

## Guard design constraints

- **OC-1**: Guard imports no runtime-specific modules (python_runner, mcp_runner, etc.). It is a pure decision layer.
- **OC-2**: The decision path is pure in-memory. No file I/O, no network calls. Config is cached after first load.
- **OC-3**: Internal exceptions always fail closed. The outer `check_action()` wraps `_check_action_inner()` in a try/except that returns deny (strict) or prompt (default) on any unhandled error.
- **Policy resolution order**: tool_override > global action_policy > default. Strict mode replaces the effective policy layer entirely — tool overrides are ignored.
- **Critical risk is unoverridable**: No policy configuration, tool override, or agent pre-approval can allow a critical-risk tool call.

## What Guard does NOT protect against

| Limitation | Explanation |
|---|---|
| **Runtime enforcement** | Guard classifies and gates; it does not sandbox. A tool allowed by guard can still do anything the subprocess allows. |
| **Manifest accuracy** | Guard trusts the manifest's `action_type` declarations. A malicious publisher could declare `read` for a tool that actually deletes data. |
| **Semantic mismatch** | The name heuristic (`delete_*` → delete) may misclassify tools with unconventional naming. |
| **Prompt fatigue** | Workflows with many high-risk tools generate repeated confirmation prompts. Per-tool overrides mitigate this but require explicit opt-in. |
| **Rate limit bypass** | Rate limits are in-memory, per-process. Restarting the process resets all counters. |

## Future work

- Registry response signing — registry-level cryptographic guarantees (TG-4)
- Global lockfile hash — detect entry addition/removal
- Subprocess filesystem isolation (workspace-only mode)
- Network namespace isolation (Linux)
- Container-based sandbox for high-risk packages

See `TRUST_STACK.md` for the full trust architecture overview and gap analysis.
