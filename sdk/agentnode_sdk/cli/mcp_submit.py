"""Submit an MCP manifest to the AgentNode registry.

Runs verification locally, then sends manifest + report to the backend.
Requires a publisher account and API key.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import yaml

from agentnode_sdk.cli.mcp_verify import verify_manifest


def _resolve_api_base() -> str:
    from agentnode_sdk.client import DEFAULT_BASE_URL
    return os.environ.get("AGENTNODE_API_URL", DEFAULT_BASE_URL).rstrip("/")


def cmd_mcp_submit(
    path_str: str,
    *,
    token: str | None = None,
    test: bool = False,
    dry_run: bool = False,
) -> int:
    """Submit an MCP server for catalog review."""
    manifest_path = Path(path_str).resolve()
    if manifest_path.is_dir():
        manifest_path = manifest_path / "agentnode.yaml"

    if not manifest_path.exists():
        print(f"  Error: {manifest_path} not found", file=sys.stderr)
        return 1

    # Step 1: Read manifest
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  Error: Failed to parse agentnode.yaml: {e}", file=sys.stderr)
        return 1

    if not isinstance(manifest, dict):
        print("  Error: agentnode.yaml root must be a mapping.", file=sys.stderr)
        return 1

    mcp = manifest.get("mcp_server", {})
    pkg_name = mcp.get("npm_package") or mcp.get("pypi_package") or "unknown"

    print()
    print("  AgentNode MCP Submit")
    print("  " + "-" * 22)
    print(f"  Package:  {pkg_name}")
    print()

    # Step 2: Run verification
    print("  Running verification...")
    report = verify_manifest(manifest_path, run_test=test)
    report_dict = report.to_dict()

    print(f"  Status:   {report.status}")
    print(f"  Summary:  {report.summary}")

    if report.actions:
        high = sum(1 for a in report.actions if a.severity == "high")
        med = sum(1 for a in report.actions if a.severity == "medium")
        if high:
            print(f"  Actions:  {high} required, {med} recommended")
        elif med:
            print(f"  Actions:  {med} recommended")

    if report.status == "INVALID":
        print()
        print("  Cannot submit: verification has blocking errors.")
        print("  Run: agentnode mcp verify . to see details.")
        return 1

    print()

    if dry_run:
        print("  Dry run -- not submitting.")
        print("  Verification report generated successfully.")
        print(f"  To submit: agentnode mcp submit {path_str}")
        print()
        return 0

    # Step 3: Auth
    api_key = token or os.environ.get("AGENTNODE_API_KEY", "")
    if not api_key:
        print(
            "  Error: No AgentNode API key configured.\n\n"
            "  Create an account at https://agentnode.net,\n"
            "  then create an API key in your dashboard.\n\n"
            "  Set it with:\n"
            "    export AGENTNODE_API_KEY=...\n\n"
            "  or run:\n"
            f"    agentnode mcp submit {path_str} --token <key>\n",
            file=sys.stderr,
        )
        return 1

    # Step 4: Submit
    api_base = _resolve_api_base()
    url = f"{api_base}/v1/mcp/submit"
    api_host = api_base.replace("https://", "").replace("http://", "")

    print(f"  Submitting to {api_host}...")

    try:
        resp = httpx.post(
            url,
            json={
                "manifest": manifest,
                "verification_report": report_dict,
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
    except Exception as e:
        print(f"  Error: Connection failed: {e}", file=sys.stderr)
        return 1

    if resp.status_code == 201:
        data = resp.json()
        sub_id = data.get("id", "?")
        print(f"  Submitted: {data.get('package_name', pkg_name)}")
        print(f"  ID:        {sub_id}")
        print(f"  Status:    {data.get('status', '?')}")
        print(f"  Message:   {data.get('message', '')}")
        print()
        print(f"  Check status: agentnode mcp status {sub_id}")
        print("  Your MCP is queued for review and won't be listed until approved.")
        print()
        return 0

    # Error handling
    try:
        err = resp.json()
        code = err.get("code", "")
        message = err.get("message", resp.text)
    except Exception:
        code = ""
        message = resp.text

    if resp.status_code == 409:
        print(f"  {message}", file=sys.stderr)
    elif resp.status_code == 401:
        print("  Error: Authentication failed. Check your API key.", file=sys.stderr)
    elif resp.status_code == 403:
        if "PUBLISHER" in code:
            print("  Error: You need a publisher profile.", file=sys.stderr)
            print("  Create one at https://agentnode.net/dashboard", file=sys.stderr)
        else:
            print(f"  Error: {message}", file=sys.stderr)
    elif resp.status_code == 400:
        print(f"  Error: {message}", file=sys.stderr)
    else:
        print(f"  Error ({resp.status_code}): {message}", file=sys.stderr)

    print()
    return 1
