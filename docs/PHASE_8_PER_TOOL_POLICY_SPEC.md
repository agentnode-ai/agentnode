# Phase 8 — Per-Tool Policy Design Spec

Status: Draft
Date: 2026-05-18

---

## 1. Problem

Guard policies today are global by action type: `guard.delete = "prompt"` applies uniformly to every tool in every package. A file manager's `list_files` (read) and `delete_file` (delete) are governed by the same `guard.delete` policy.

This is too coarse:
- A user who trusts `file-manager/list_files` must still globally allow `read` for all packages.
- There is no way to deny a specific dangerous tool without denying that action type everywhere.
- Package authors cannot declare that a specific tool should require elevated confirmation.

## 2. Design Decisions (agreed)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Resolution hierarchy | 3-tier: tool_override > global action_policy > default | Package-level overrides deferred — keeps config flat |
| Config key format | `guard.tool_overrides.{slug/tool_name}.{action_type}` | Flat, unambiguous, grep-friendly, wildcard-ready later |
| Per-tool rate limits | Out of scope | Different data structure, different UX — separate block later |
| Package-level overrides | Out of scope | Can layer in later without breaking this schema |
| Wildcard tool policies | Out of scope | Complexity without clear use case yet |

## 3. Config Schema

### 3.1 New structure

```json
{
  "guard": {
    "read": "allow",
    "delete": "prompt",
    "execute": "prompt",
    "tool_overrides": {
      "web-scraper/fetch_page": {
        "network_egress": "deny"
      },
      "file-manager/delete_file": {
        "delete": "deny",
        "write_local": "deny"
      }
    }
  }
}
```

### 3.2 Key format

Tool override keys use `{slug}/{tool_name}` as the compound key:
- `slug` = installed package slug (same as lockfile key)
- `tool_name` = tool name as declared in the package manifest `tools[].name`
- Separator: `/` (forward slash) — already used in MCP tool naming, familiar

### 3.3 Validation rules

- CLI warns if `slug/tool_name` does not match an installed package + tool, but does not reject (package might be temporarily removed)
- Resolution silently ignores overrides for unknown tools — no warnings in the decision path, no audit spam
- Each override value must be `"allow"`, `"prompt"`, or `"deny"`
- Only keys in `ACTION_TYPES` are valid as override keys within a tool entry
- Unknown action_type keys within a tool override are silently ignored during resolution and warned during CLI mutation — keeps runtime robust against old configs, future action types, and manual JSON edits
- `tool_name` must not contain `/` — ensures the compound key `slug/tool_name` is unambiguous
- Empty tool override dicts (`{}`) are valid but no-ops

## 4. Resolution Chain

```
resolve_policy(action_type, slug, tool_name):
  1. if tool_override exists for "{slug}/{tool_name}".{action_type}:
       return tool_override value
  2. if global guard.{action_type} is set:
       return global value
  3. return _DEFAULT_GUARD_POLICY[action_type]
```

Strict mode replaces the effective policy layer before tool override resolution. When strict mode is active, the entire resolution chain is bypassed — `_STRICT_GUARD_POLICY` is used directly.

### 4.1 Interaction with existing mechanisms

| Mechanism | Priority | Unchanged? |
|-----------|----------|------------|
| Critical risk (score) | Highest — always deny | Yes |
| Strict mode | Replaces effective policy layer entirely | Yes — tool_overrides never evaluated |
| Install/run policy denial | Before guard — blocks execution | Yes |
| Tool override | New — above global policy | New |
| Agent pre_approved_actions | Within agent flow only | Yes |
| Global action policy | Current behavior | Yes |
| Default | Fallback | Yes |

**Hard ceiling:** Tool overrides never bypass:
- Critical risk deny (risk_level == "critical")
- Strict mode (`AGENTNODE_GUARD_STRICT=true`)
- Install/run policy denial (check_run rejects before guard runs)

A tool override `allow` means "allow at the policy layer" — not "absolute allow." The risk engine and strict mode remain above it.

### 4.2 Credential_use special handling

`credential_use` has existing special logic (connector_declared bypass). Tool overrides for `credential_use` take priority:
- If tool override says `deny` → deny (no connector bypass)
- If tool override says `allow` → allow (no prompt)
- If no tool override → existing connector_declared logic applies

The connector_declared bypass is evaluated only when no tool override exists for that slug/tool_name + `credential_use`.

## 5. Guard Chain Tracing

New chain entry format for tool overrides:
```
guard_action:deny(delete:tool_override[file-manager/delete_file])
```

This extends the existing pattern:
```
guard_action:deny(delete)                                          # global
guard_action:deny(delete:tool_override[file-manager/delete_file])  # tool override
```

## 6. CLI Changes

### 6.1 `agentnode guard set` — extended syntax

```
agentnode guard set <action_type> <value>                         # global (unchanged)
agentnode guard set <action_type> <value> --tool <slug/tool_name> # per-tool (new)
```

Examples:
```
agentnode guard set delete deny --tool file-manager/delete_file
agentnode guard set network_egress deny --tool web-scraper/fetch_page
```

### 6.2 `agentnode guard policy` — extended display

```
Guard Policy
  read            allow
  compute         allow
  write_local     allow
  network_egress  allow
  write_external  prompt
  delete          prompt
  execute         prompt
  credential_use  prompt
  unknown         prompt

Tool Overrides
  file-manager/delete_file
    delete          deny
    write_local     deny
  web-scraper/fetch_page
    network_egress  deny
```

JSON output adds `"tool_overrides": {...}` to the existing `get_resolved_policy()` return value.

### 6.3 `agentnode guard reset`

Reset clears `tool_overrides` entirely (restores to `{}`). Consistent with "reset to defaults."

### 6.4 `agentnode guard unset`

Two granularity levels:

```
agentnode guard unset --tool file-manager/delete_file               # remove all overrides for tool
agentnode guard unset delete --tool file-manager/delete_file         # remove single action_type override
```

Without a specific action_type: removes the entire tool entry from `tool_overrides`.
With an action_type: removes only that key. If the tool entry becomes empty, removes it.

## 7. Affected Modules

| Module | Change |
|--------|--------|
| `guard.py` | `_load_guard_config()` loads `tool_overrides` into cache; `_check_action_inner()` resolves per-tool before global; `get_resolved_policy()` includes overrides |
| `config.py` | `_merge_defaults()` preserves `tool_overrides` (already preserves extra guard keys) |
| `cli/commands.py` | `cmd_guard_set()` accepts `--tool`; `cmd_guard_policy()` displays overrides; `cmd_guard_reset()` clears overrides; new `cmd_guard_unset()` |
| `cli/main.py` | Parser changes for `--tool` flag and `unset` subcommand |

## 8. Implementation Phases

| Phase | Scope | Tests |
|-------|-------|-------|
| 8.1 Config + Resolution | `_load_guard_config()` loads tool_overrides, `_check_action_inner()` resolves per-tool before global, `get_resolved_policy()` includes overrides | Unit: override wins, global fallback, strict ignores override, empty override, invalid keys ignored, credential_use interaction, unknown tool silently ignored |
| 8.2 CLI | `guard set --tool`, `guard unset`, `guard policy` display, `guard reset` clears overrides | CLI output, persistence, round-trip, granular unset, error messages, cache invalidation |
| 8.3 Audit + Chain | Guard chain entries show tool_override source, audit entries include tool context | Chain format, audit filtering by tool |

### Acceptance criteria per phase

**8.1:**
- Tool override `deny` blocks even if global says `allow`
- Tool override `allow` passes even if global says `prompt` (at policy layer only — does not bypass critical risk or strict mode)
- No tool override → existing behavior exactly preserved
- Strict mode replaces the effective policy layer — tool overrides never evaluated
- `credential_use` tool override takes priority over connector bypass
- Resolution silently ignores overrides for unknown/uninstalled tools
- Resolution silently ignores unknown action_type keys within tool overrides
- Config round-trip: load → save → load preserves tool_overrides
- OC-2 preserved: no file I/O in decision path (tool_overrides loaded into cached dict)
- OC-3 preserved: malformed tool_overrides → fail-closed

**8.2:**
- `guard set X Y --tool slug/name` persists and takes effect
- `guard unset --tool slug/name` removes all overrides for that tool
- `guard unset X --tool slug/name` removes single action_type override
- `guard policy` shows tool overrides section (omitted when empty)
- `guard reset` clears all tool overrides
- `guard set`, `guard unset`, and `guard reset` all invalidate guard config cache
- Invalid tool key format → clear error message
- Invalid action type / value → same errors as global set
- CLI warns if tool key does not match an installed package+tool

**8.3:**
- Guard chain distinguishes `tool_override` from global policy source
- Audit log includes tool-level context when tool override was the deciding factor

## 9. Invariants

- OC-1: No runtime imports in guard.py (unchanged)
- OC-2: Decision path is pure in-memory (unchanged — tool_overrides cached alongside global policy)
- OC-3: Exceptions → fail-closed (unchanged)
- Strict mode is absolute — no config override can weaken it
- Tool override `allow` is bounded — critical risk and strict mode remain above it
- Tool override can only set allow/prompt/deny — cannot set rate limits, cannot set risk scores
- An empty `tool_overrides` dict is the default — zero behavioral change for existing users
- Cache invalidation on every mutation (set, unset, reset)
- Tool overrides are resolved per action_type independently — an override for `delete` does not imply any policy for `write_local`, `execute`, or other action types on the same tool

## 10. Out of Scope

- Per-tool rate limits (separate config structure, separate UX)
- Package-level policy overrides (can layer between tool and global later)
- Wildcard tool policies (`web-scraper/*`)
- Manifest-declared tool policy hints (would need trust model changes)
- SQL/prompt injection heuristics (separate concern, needs false-positive analysis)
