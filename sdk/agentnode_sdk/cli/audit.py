"""CLI command: agentnode audit — display recent policy decisions.

Also provides ``read_audit_entries()`` as the shared entry point for
reading audit.jsonl. All CLI commands that need audit data should use
this function instead of reading the file directly.
"""
from __future__ import annotations

import json
from typing import Any


def read_audit_entries(
    *,
    limit: int | None = None,
    event: str | None = None,
    slug: str | None = None,
    action: str | None = None,
    tool_name: str | None = None,
) -> list[dict[str, Any]]:
    """Read sanitized audit entries from ~/.agentnode/audit.jsonl.

    Skips malformed JSON lines. Returns entries newest-last.
    Each entry is sanitized — only policy metadata, no secrets.
    Filters are applied before the limit.

    Args:
        limit: Maximum number of entries to return (from the end).
            None means return all entries.
        event: Filter by event type (exact match).
        slug: Filter by package slug (exact match).
        action: Filter by decision action (exact match, case-insensitive).
        tool_name: Filter by tool name (exact match).
    """
    from agentnode_sdk.config import config_dir

    audit_path = config_dir() / "audit.jsonl"
    if not audit_path.is_file():
        return []

    try:
        raw = audit_path.read_text(encoding="utf-8").strip()
    except OSError:
        return []

    if not raw:
        return []

    entries: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            entries.append(_sanitize_entry(json.loads(line)))
        except json.JSONDecodeError:
            continue

    if event is not None:
        entries = [e for e in entries if e.get("event") == event]
    if slug is not None:
        entries = [e for e in entries if e.get("slug") == slug]
    if action is not None:
        action_lower = action.lower()
        entries = [e for e in entries if (e.get("action") or "").lower() == action_lower]
    if tool_name is not None:
        entries = [e for e in entries if e.get("tool_name") == tool_name]

    if limit is not None:
        entries = entries[-limit:]

    return entries


def cmd_audit(
    limit: int = 20,
    json_output: bool = False,
    event: str | None = None,
    slug: str | None = None,
    action: str | None = None,
    tool_name: str | None = None,
) -> int:
    from agentnode_sdk.config import config_dir
    from agentnode_sdk.cli.output import bold, dim

    audit_path = config_dir() / "audit.jsonl"
    if not audit_path.is_file():
        print("\n  No audit log found.\n")
        return 0

    entries = read_audit_entries(
        limit=limit,
        event=event,
        slug=slug,
        action=action,
        tool_name=tool_name,
    )

    if not entries:
        print("\n  No matching audit entries.\n")
        return 0

    if json_output:
        print(json.dumps(entries, indent=2))
        return 0

    print()
    print(f"  {bold('Guard & Policy Audit Log')}")
    print(f"  {'─' * 24}")
    print()
    for e in entries:
        ts = e.get("ts", "")[:19]
        evt = _short_event(e.get("event", "?"))
        act = (e.get("action") or "?").upper()
        pkg = e.get("slug") or "?"
        tool = e.get("tool_name")
        trust = e.get("trust") or "?"

        target = f"{pkg}::{tool}" if tool else pkg
        act_fmt = _format_action(act)

        print(f"  {dim(ts)}  {evt:<16} {act_fmt:<8} {target:<32} {dim(trust)}")

        if act in ("DENY", "PROMPT"):
            reason = e.get("reason", "")
            source = e.get("source", "")
            if reason:
                print(f"  {' ' * 21}{' ' * 16}{dim('↳ ' + reason)}")
            if source:
                print(f"  {' ' * 21}{' ' * 16}{dim('  source: ' + source)}")

        if e.get("event") == "guard_confirmation":
            reason = e.get("reason", "")
            if reason and act == "ALLOW":
                print(f"  {' ' * 21}{' ' * 16}{dim('↳ ' + reason)}")

    print()
    return 0


_EVENT_SHORT = {
    "run_tool": "run_tool",
    "guard_check": "guard_check",
    "guard_rate_limit": "guard_rate_limit",
    "guard_confirmation": "guard_confirm",
    "runtime_run": "runtime_run",
    "client_install": "client_install",
    "mcp_run": "mcp_run",
    "agent_run": "agent_run",
    "remote_run": "remote_run",
}


def _short_event(event: str) -> str:
    return _EVENT_SHORT.get(event, event)


def _format_action(action: str) -> str:
    from agentnode_sdk.cli.output import _colors_enabled
    if not _colors_enabled():
        return action
    colors = {"ALLOW": "\033[32m", "DENY": "\033[31m", "PROMPT": "\033[33m"}
    color = colors.get(action, "")
    return f"{color}{action}\033[0m" if color else action


def guard_status_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate guard audit entries into a status summary.

    Pure computation — no I/O, no side effects.
    """
    from collections import Counter
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    cutoff_24h = (now - timedelta(hours=24)).isoformat()
    cutoff_7d = (now - timedelta(days=7)).isoformat()

    def _in_window(entry: dict, cutoff: str) -> bool:
        ts = entry.get("ts", "")
        return ts >= cutoff if ts else False

    entries_24h = [e for e in entries if _in_window(e, cutoff_24h)]
    entries_7d = [e for e in entries if _in_window(e, cutoff_7d)]

    def _count_actions(subset: list[dict]) -> dict[str, int]:
        c: Counter = Counter()
        for e in subset:
            c[e.get("action", "unknown")] += 1
        return dict(c)

    def _top_denied(subset: list[dict], n: int = 5) -> list[dict[str, Any]]:
        c: Counter = Counter()
        for e in subset:
            if e.get("action") == "deny":
                slug = e.get("slug") or "unknown"
                c[slug] += 1
        return [{"slug": s, "count": cnt} for s, cnt in c.most_common(n)]

    def _top_prompted_actions(subset: list[dict], n: int = 5) -> list[dict[str, Any]]:
        c: Counter = Counter()
        for e in subset:
            if e.get("action") == "prompt":
                source = e.get("source", "")
                if source.startswith("guard.action_policy."):
                    action_type = source.split(".")[-1]
                    c[action_type] += 1
                else:
                    c[source or "unknown"] += 1
        return [{"action_type": a, "count": cnt} for a, cnt in c.most_common(n)]

    def _rate_limit_hits(subset: list[dict]) -> int:
        return sum(1 for e in subset if e.get("event") == "guard_rate_limit")

    return {
        "total_entries": len(entries),
        "period_24h": {
            "total": len(entries_24h),
            "actions": _count_actions(entries_24h),
            "top_denied": _top_denied(entries_24h),
            "top_prompted_actions": _top_prompted_actions(entries_24h),
            "rate_limit_hits": _rate_limit_hits(entries_24h),
        },
        "period_7d": {
            "total": len(entries_7d),
            "actions": _count_actions(entries_7d),
            "top_denied": _top_denied(entries_7d),
            "top_prompted_actions": _top_prompted_actions(entries_7d),
            "rate_limit_hits": _rate_limit_hits(entries_7d),
        },
    }


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
