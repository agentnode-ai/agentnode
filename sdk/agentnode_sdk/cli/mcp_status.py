"""Check MCP submission status."""
from __future__ import annotations

import json
import os
import sys

import httpx


STATUS_DISPLAY = {
    "pending": ("Under Review", "Your submission is queued for review."),
    "action_required": ("Action Required", "Your manifest has issues from verification. Fix and resubmit."),
    "needs_changes": ("Changes Requested", "The reviewer has requested changes. See feedback below."),
    "approved": ("Approved", "Your MCP has been approved."),
    "rejected": ("Not Accepted", "Your submission was not accepted. See feedback below."),
}


def _resolve_api_base() -> str:
    from agentnode_sdk.client import DEFAULT_BASE_URL
    return os.environ.get("AGENTNODE_API_URL", DEFAULT_BASE_URL).rstrip("/")


def cmd_mcp_status(
    submission_id: str,
    *,
    token: str | None = None,
    json_output: bool = False,
) -> int:
    """Check the status of an MCP submission."""
    api_key = token or os.environ.get("AGENTNODE_API_KEY", "")
    if not api_key:
        print("  Error: No API key. Set AGENTNODE_API_KEY or use --token.", file=sys.stderr)
        return 1

    api_base = _resolve_api_base()
    url = f"{api_base}/v1/mcp/submissions/{submission_id}"

    try:
        resp = httpx.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
    except Exception as e:
        print(f"  Error: {e}", file=sys.stderr)
        return 1

    if resp.status_code == 404:
        print("  Submission not found or not owned by your publisher.", file=sys.stderr)
        return 1
    if resp.status_code == 401:
        print("  Authentication failed. Check your API key.", file=sys.stderr)
        return 1
    if not resp.is_success:
        print(f"  Error ({resp.status_code}): {resp.text[:200]}", file=sys.stderr)
        return 1

    data = resp.json()

    if json_output:
        print(json.dumps(data, indent=2))
        return 0

    status = data.get("status", "?")
    display_label, display_hint = STATUS_DISPLAY.get(status, (status, ""))
    pkg = data.get("package_name", "?")
    ver = data.get("package_version", "")

    print()
    print("  MCP Submission Status")
    print("  " + "-" * 24)
    print(f"  Package:    {pkg}{'@' + ver if ver else ''}")
    print(f"  Status:     {display_label}")
    if display_hint:
        print(f"              {display_hint}")

    if data.get("report_summary"):
        print(f"  Verify:     {data['report_summary']}")

    if data.get("reviewed_at"):
        print(f"  Reviewed:   {data['reviewed_at'][:10]}")

    feedback = data.get("maintainer_feedback")
    if feedback:
        print()
        print("  Reviewer Feedback:")
        for line in feedback.strip().splitlines():
            print(f"    {line}")

    actions = data.get("actions") or []
    high = [a for a in actions if a.get("severity") == "high"]
    if high and status in ("action_required", "needs_changes"):
        print()
        print("  Required Actions:")
        for a in high:
            print(f"    [{a.get('code', '?')}] {a.get('title', '')}")
            if a.get("fix"):
                print(f"      Fix: {a['fix']}")

    if status == "approved" and not data.get("published_package_id"):
        print("\n  Approved. Awaiting catalog publication.")
    elif status == "approved" and data.get("published_package_id"):
        print(f"\n  Published: https://agentnode.net/packages/{pkg}")

    if status in ("needs_changes", "rejected"):
        print("\n  To resubmit: fix the issues above, then run agentnode mcp submit again.")

    print()
    return 0
