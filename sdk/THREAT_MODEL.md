# AgentNode SDK — Threat Model

Last updated: 2026-05-08

## Scope

This document covers the AgentNode SDK's local execution model — the code that runs on a developer's machine when they install and run agent tools. It does **not** cover the AgentNode registry backend or web application.

## What AgentNode enforces

| Protection | How | Where |
|---|---|---|
| **Pre-execution policy gate** | `check_run()` evaluates trust level, permissions, and environment before every tool execution. Returns allow/deny/prompt. | `policy.py` |
| **Environment variable filtering** | Subprocess mode runs tools with a stripped environment. Only `PATH`, `HOME`, `PYTHON*`, `TEMP`, and `AGENTNODE_LOCKFILE` are passed. API keys (`AWS_*`, `OPENAI_*`, `STRIPE_*`, etc.) are excluded. | `python_runner.py` |
| **Subprocess timeout** | Tools in subprocess mode are killed after a configurable timeout (default 30s). | `python_runner.py` |
| **Trust level minimum** | Users set a minimum trust level (default: `verified`). Packages below this threshold are denied. | `policy.py` |
| **Non-interactive deny** | In CI/non-interactive environments, any `prompt` decision escalates to `deny`. | `policy.py` |
| **Fail-closed on broken config** | If `config.json` is missing or invalid, policy defaults to deny (non-interactive) or prompt (interactive). | `policy.py` |
| **Trust refresh** | Trust levels are re-fetched from the registry every 7 days. | `runner.py` |
| **Audit trail** | All policy decisions are logged to `~/.agentnode/audit.jsonl`. Append-only, rotated, local-only. No secrets logged. | `policy.py` |
| **MCP env filtering** | MCP server subprocesses use the same allowlist-based environment filtering. | `mcp_runner.py` |
| **Credential domain lock** | `CredentialHandle` validates the target domain against `allowed_domains` before attaching credentials. Secrets are not exposed via properties. | `credential_handle.py` |
| **Agent tool allowlist** | Agent packages can only call tools explicitly listed in their manifest. | `agent_runner.py` |

## What AgentNode does NOT enforce

| Threat | Current state | User mitigation |
|---|---|---|
| **Network access by tools** | Declared in manifest, checked by policy gate, but **not restricted at runtime**. A tool declaring `network: none` can still make HTTP requests. | Review permissions before installing. Use `agentnode inspect` to see declared permissions. |
| **Filesystem access by tools** | Same as network — declared, policy-checked, but not sandboxed. | Review permissions. Run in a VM or container for sensitive workloads. |
| **Direct mode env access** | `mode="direct"` runs tool code in the same process with full environment access, including API keys. | Use `mode="auto"` (default), which always resolves to subprocess. |
| **`load_tool()` bypass** | Calling `load_tool()` directly skips all policy checks and audit logging. | Use `run_tool()` instead, which goes through the full policy pipeline. |
| **Malicious package code** | Trust level and verification reduce risk but do not prevent a determined attacker. | Only install packages from trusted publishers. Review source code for sensitive use cases. |
| **Inter-tool data leakage** | Tools in the same subprocess session share the filtered environment. | No current mitigation. |

## Privacy boundary

- **Local execution**: All tool execution happens on the user's machine. No tool inputs, outputs, or logs are sent to AgentNode.
- **What the registry sees**: Install events, search queries, trust-level refresh requests.
- **Audit logs**: Stored at `~/.agentnode/audit.jsonl`, never transmitted. Contains only policy decisions (action, source, reason, trust level), never tool arguments or results.

## Architecture summary

```
User calls run_tool()
  → Policy kernel: check_run() — allow / deny / prompt
  → Risk policies: check_risk_policies() — flag-based reactions
  → Input guard: check_inputs() — warnings only, never blocks
  → Runtime dispatch:
      → Subprocess (default): filtered env, timeout, tmpdir
      → Direct: in-process, full env (explicit opt-in only)
      → MCP: subprocess with filtered env, JSON-RPC over stdio
      → Remote: HTTPS with CredentialHandle, domain-locked
      → Agent: orchestrator with tool allowlist, iteration limits
```

## Future work

- Subprocess filesystem isolation (workspace-only mode)
- Network namespace isolation (Linux)
- Container-based sandbox for high-risk packages
