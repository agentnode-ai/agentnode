# AgentNode Guard: Pre-Execution Policy for AI Agents

**Category:** Engineering
**Tags:** security, policy, guard, SDK
**Slug:** introducing-agentnode-guard
**Excerpt:** AgentNode Guard is the policy gateway built into the SDK. Every install and run passes through Guard before anything executes — enforcing trust levels, permission boundaries, and environment context with a complete audit trail.

---

## The Problem

When an AI agent installs and runs third-party tool packages, who decides what's allowed?

Most agent frameworks treat this as an afterthought — security checks are optional, scattered across different code paths, or left entirely to the developer. The result: either everything is allowed (unsafe), or developers build their own permission layer from scratch (expensive, inconsistent).

AgentNode Guard is a different approach. Instead of bolting security on after the fact, Guard is the policy kernel built into the SDK itself. Every execution path passes through it. There is no bypass.

## What Guard Does

Guard is a pre-execution policy gateway. Before any tool package is installed or run, Guard evaluates three things:

1. **Trust Level** — Does the package meet the minimum trust threshold configured by the user? An unverified package can't run in an environment that requires verified or higher.

2. **Permissions** — Does the package's declared permission set (network, filesystem, code execution) align with what the user's policy allows? A package requesting unrestricted network access will be denied if the config says `"network": "deny"`.

3. **Environment Context** — Is the runtime environment sensitive? Guard detects whether secrets are present (AWS keys, API tokens, database URLs), whether code is running in CI, and whether the process runs as root. In sensitive environments, Guard escalates decisions — what would normally be "allow" becomes "prompt" or "deny".

Based on these checks, Guard returns one of three actions:

- **allow** — the operation proceeds
- **deny** — the operation is blocked with a reason
- **prompt** — the operation requires user confirmation (in non-interactive environments like MCP or CI, prompt becomes deny automatically)

## Fail-Closed by Design

Guard's most important property is what happens when things go wrong.

If the user's config file is missing or corrupted, Guard does not default to allow. It defaults to **prompt** (interactive) or **deny** (non-interactive). This is the fail-closed principle — uncertainty means stop, not proceed.

If Guard's own code crashes (a bug in the policy engine), the install or run is denied and the error is logged. Prior to Guard's hardening, a crash in the policy check would silently pass — the `try/except pass` pattern meant that a broken config provided zero protection. This has been fixed: policy enforcement is now authoritative across all execution paths.

## Where Guard Runs

Guard is not a separate service. It's a Python module (`policy.py`) that is called from every execution path in the SDK:

| Path | What Guard Checks |
|------|-------------------|
| `client.install()` | Trust level, permissions before downloading |
| `runner.run_tool()` | Trust, permissions, environment context before execution |
| `runtime.handle()` | Same as runner, for the runtime dispatch layer |
| MCP `call_tool()` | Same checks, non-interactive (prompt → deny) |
| Agent runner | Trust verification for multi-step agent execution |
| Remote runner | Audited as `remote_run` event for external API calls |

There is no execution path that bypasses Guard. This is enforced structurally, not by convention.

## The Audit Trail

Every decision Guard makes is logged to `~/.agentnode/audit.jsonl` — an append-only JSONL file with one record per decision:

```json
{
  "ts": "2026-04-16T14:23:01.123456+00:00",
  "event": "client_install",
  "slug": "pdf-reader-pack",
  "action": "allow",
  "source": "default",
  "reason": "All checks passed",
  "trust": "trusted",
  "env": "linux/user/no_ci/no_secrets"
}
```

Each record includes:
- **Timestamp** in UTC
- **Event type** — which execution path triggered the check (`run_tool`, `client_install`, `mcp_run`, `remote_run`, etc.)
- **Action** — what Guard decided
- **Source** — which rule made the decision (`trust_level`, `permission.network`, `environment.has_secrets`, `default`)
- **Environment summary** — compact representation of OS, privilege level, CI status, and secret presence (only booleans — no secret values are ever logged)

The audit file auto-rotates when it exceeds a configurable size (default: 10 MB), keeping up to 5 rotated files. This prevents unbounded growth on long-running systems.

### Reading the Audit Trail

The CLI provides three commands:

```bash
# Show the last 20 decisions
agentnode audit show

# Show the last 100 decisions as JSON
agentnode audit show --limit 100 --json

# Summary statistics
agentnode audit stats

# Clear the audit log
agentnode audit clear --yes
```

`agentnode audit stats` shows action breakdowns (how many allow/deny/prompt), event type distribution, top packages, and the time period covered.

## Configuration

Guard reads its policy from `~/.agentnode/config.json`:

```json
{
  "trust": {
    "minimum_trust_level": "verified"
  },
  "permissions": {
    "network": "prompt",
    "filesystem": "prompt",
    "code_execution": "sandboxed"
  },
  "audit": {
    "max_size_mb": 10,
    "max_files": 5
  }
}
```

**Trust** sets the floor. Packages below the minimum are denied outright. For most setups, `"verified"` is the right default — it requires confirmed publisher identity and passed security scans.

**Permissions** control what capabilities are allowed without asking. `"allow"` permits silently, `"prompt"` asks for confirmation, `"deny"` blocks outright. `"sandboxed"` for code execution allows subprocess-based execution but prompts for unrestricted.

**Audit** controls log rotation. The defaults (10 MB, 5 files) are suitable for most deployments.

### Strict Mode

For CI pipelines or production environments where no prompts are possible, set:

```bash
export AGENTNODE_GUARD_STRICT=true
```

This converts all `prompt` decisions to `deny`. Combined with `non-interactive` detection (Guard detects CI via `GITHUB_ACTIONS`, `GITLAB_CI`, etc.), this ensures that uncertain decisions always fail closed.

## What Guard Does Not Do

Guard is a policy enforcement layer, not a sandbox. It does not:

- **Intercept system calls** — Guard decides whether to start execution, not what happens during execution
- **Inspect tool arguments** — Guard checks trust, permissions, and environment. Argument-level policy is a future extension
- **Replace the permission model** — Guard enforces the existing permission declarations. It doesn't invent new ones
- **Run as a separate service** — Guard is in-process, zero-latency. No network calls, no dependencies

## Why This Matters

AI agents are becoming autonomous enough to install and run tools without human intervention. The question is not whether they need a policy layer — they do. The question is whether that layer is authoritative or advisory.

Guard is authoritative. It's not a suggestion engine that logs warnings. It's a gate that stops execution when the policy says no. And because it's built into the SDK rather than wrapped around it, there is no way to accidentally bypass it.

Every `client.install()`, every `run_tool()`, every MCP `call_tool()` goes through Guard. The audit trail proves it.

---

*Guard is available today in the AgentNode SDK. Configure your policy in `~/.agentnode/config.json`, run your agent, and check `agentnode audit show` to see Guard in action.*
