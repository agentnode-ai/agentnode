# Phase 9 — Guard Check (Policy Dry-Run)

Status: Draft
Date: 2026-05-18

---

## 1. Problem

Users can see the resolved policy (`guard policy`) and audit past decisions (`guard status`, `audit`), but cannot preview what the guard would decide for a specific tool before running it. Debugging policy misconfigurations requires trial and error.

## 2. Command

```
agentnode guard check <slug/tool_name> [--action <action_type>] [--json]
```

### What it does

Runs `check_action()` against the installed lockfile entry with empty kwargs. Displays the full decision path: classification, risk, resolution chain, and final action. No side effects.

### What it does NOT do

- No registry fetch — only installed packages
- No synthetic entry construction — uses real lockfile entry
- No audit write — purely read-only
- No rate limit check — rate state is per-session, not useful for dry-run
- No MCP argument inspection — no args to inspect
- No config mutation

## 3. Inputs and Behavior

| Input | Behavior |
|-------|----------|
| Installed slug + valid tool_name | Full dry-run, show decision |
| Installed slug + unknown tool_name | Warning: "tool_name not found in installed metadata. Using name heuristic only." Exit 0, proceed with heuristic |
| Non-installed slug | Error: "Package '{slug}' not installed" |
| Missing `/` in key | Error: "Expected slug/tool_name format" |
| Skill package | Info: "{slug} is a skill — skills bypass guard" |
| `--action delete` | Override action classification with exactly one action type — does not modify manifest or config. No CSV, no multiple values. |
| `--action` with invalid type | Error: same as `guard set` validation |

## 4. Output Format

### Text (default)

```
Guard Check: file-manager/delete_file

  Action Types      delete (manifest)
  Trust Level       verified
  Risk Level        medium

  Resolution Chain
    guard_action:deny(delete:tool_override[file-manager/delete_file])

  Decision          deny
  Source            guard.tool_override.file-manager/delete_file
  Reason            Action type 'delete' denied by tool override [file-manager/delete_file]
```

With `--action` override:
```
Guard Check: file-manager/delete_file --action execute

  Action Types      execute (override)
  ...
```

Chain entries are displayed verbatim from `GuardDecision.guard_chain` — no parsing, no interpretation layer.

### JSON (`--json`)

```json
{
  "slug": "file-manager",
  "tool_name": "delete_file",
  "action_types": ["delete"],
  "action_types_source": "manifest",
  "trust_level": "verified",
  "risk_level": "medium",
  "decision": "deny",
  "source": "guard.tool_override.file-manager/delete_file",
  "reason": "Action type 'delete' denied by tool override [file-manager/delete_file]",
  "guard_chain": ["guard_action:deny(delete:tool_override[file-manager/delete_file])"],
  "mitigations": ["Change guard policy or use a higher trust package"],
  "strict_mode": false
}
```

## 5. Implementation

### 5.1 guard.py

Add `action_types_override` parameter to `check_action()` and `_check_action_inner()`:

```python
def check_action(
    slug, tool_name, kwargs, entry, *,
    interactive=True,
    action_types_override=None,  # NEW — list[str] | None
) -> GuardDecision:
```

In `_check_action_inner()`:
```python
if action_types_override:
    action_types = list(action_types_override)
else:
    action_types = classify_action(tool_name, entry)
```

Backward-compatible: default is None, all existing callers unaffected.

### 5.2 cli/commands.py

New function `cmd_guard_check(tool_key, *, action=None, json_output=False)`:
1. Parse `slug/tool_name` from tool_key
2. Load lockfile, find entry
3. Handle skill/missing/error cases
4. Call `check_action(slug, tool_name, {}, entry, interactive=False, action_types_override=...)`
5. Also call `classify_action()` separately to determine source label (manifest vs heuristic)
6. Format output

### 5.3 cli/main.py

Add `check` subcommand to guard parser:
```python
guard_check_parser = guard_sub.add_parser("check", help="Dry-run guard check for a tool")
guard_check_parser.add_argument("tool_key", help="slug/tool_name to check")
guard_check_parser.add_argument("--action", default=None, help="Override action classification")
guard_check_parser.add_argument("--json", dest="json_output", action="store_true")
```

## 6. Tests

| Test | What |
|------|------|
| Installed tool — text output | Shows all fields: action types, trust, risk, chain, decision, source |
| Installed tool — json output | Valid JSON with all spec'd fields |
| Tool override visible in chain | Chain contains `tool_override[...]` when override active |
| Global policy visible in chain | Chain shows `guard_action:prompt(delete)` without override |
| `--action` overrides classification | Decision changes when override changes action type |
| `--action` does not persist | Subsequent check without `--action` uses real classification |
| Non-installed slug → error | Exit 1, clear message |
| Missing slash → error | Exit 1, format hint |
| Skill → bypass message | Exit 0, informational message |
| Strict mode shown | Output reflects strict mode when active |
| Invalid `--action` value → error | Exit 1, same validation as `guard set` |
| CLI routing | `main(["guard", "check", "pkg/tool"])` routes correctly |

## 7. Invariants

- No audit writes — `check_action()` does not audit, and `cmd_guard_check` must not call `_audit_guard_decision`
- No rate limit state changes — rate limit check not called
- No config mutation — `--action` override is transient, in-memory only
- OC-2 preserved — decision path remains pure in-memory
- Strict mode is visible in output — user must see when strict overrides their config

## 8. Out of Scope

- Non-installed packages (would require registry fetch + synthetic entry)
- MCP argument simulation (would need schema + test payload)
- Rate limit simulation (would need call history)
- Batch check across all tools in a package
- Interactive mode / what-if wizard
