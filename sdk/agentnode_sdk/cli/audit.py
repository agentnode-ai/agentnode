"""CLI command: agentnode audit — display recent policy decisions."""
from __future__ import annotations

import json


def cmd_audit(limit: int = 20, json_output: bool = False) -> int:
    from agentnode_sdk.config import config_dir
    from agentnode_sdk.cli.output import bold, dim, kv

    audit_path = config_dir() / "audit.jsonl"
    if not audit_path.is_file():
        print("\n  No audit log found.\n")
        return 0

    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not entries:
        print("\n  Audit log is empty.\n")
        return 0

    if json_output:
        safe = [_sanitize_entry(e) for e in entries]
        print(json.dumps(safe, indent=2))
        return 0

    print()
    print(f"  {bold('Recent Policy Decisions')}")
    print(f"  {'=' * 22}")
    print()
    for e in entries:
        ts = e.get("ts", "")[:19]
        action = e.get("action", "?").upper()
        slug = e.get("slug", "?")
        trust = e.get("trust", "?")
        source = e.get("source", "")

        action_fmt = _format_action(action)
        print(f"  {dim(ts)}  {action_fmt:<8} {slug:<28} {dim(trust):<14} {dim(source)}")
        if action == "DENY":
            reason = e.get("reason", "")
            if reason:
                print(f"  {' ' * 21}{dim('↳ ' + reason)}")
    print()
    return 0


def _format_action(action: str) -> str:
    from agentnode_sdk.cli.output import _colors_enabled
    if not _colors_enabled():
        return action
    colors = {"ALLOW": "\033[32m", "DENY": "\033[31m", "PROMPT": "\033[33m"}
    color = colors.get(action, "")
    return f"{color}{action}\033[0m" if color else action


def _sanitize_entry(entry: dict) -> dict:
    """Return audit entry with only policy metadata — no sensitive data."""
    return {
        "ts": entry.get("ts"),
        "event": entry.get("event"),
        "slug": entry.get("slug"),
        "tool_name": entry.get("tool_name"),
        "action": entry.get("action"),
        "source": entry.get("source"),
        "reason": entry.get("reason"),
        "trust": entry.get("trust"),
    }
