"""Tests for the `agentnode mcp submit` CLI output."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from agentnode_sdk.cli.mcp_submit import cmd_mcp_submit

MCP_MANIFEST = {
    "package_id": "my-mcp",
    "version": "1.0.0",
    "name": "My MCP",
    "package_type": "toolpack",
    "runtime": "mcp",
    "mcp_server": {"npm_package": "my-mcp-server", "command": "npx"},
}


def _write(pkg_dir: Path) -> None:
    (pkg_dir / "agentnode.yaml").write_text(
        yaml.dump(MCP_MANIFEST, default_flow_style=False), encoding="utf-8",
    )


def _passing_report() -> MagicMock:
    report = MagicMock()
    report.status = "RESOLVED"
    report.summary = "resolved"
    report.actions = []
    report.to_dict.return_value = {}
    return report


def test_submit_success_names_status_command(tmp_path, capsys):
    _write(tmp_path)
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {
        "id": "sub_abc123",
        "status": "pending",
        "message": "Queued for review",
        "package_name": "my-mcp-server",
    }
    with patch(
        "agentnode_sdk.cli.mcp_submit.verify_manifest",
        return_value=_passing_report(),
    ), patch("agentnode_sdk.cli.mcp_submit.httpx.post", return_value=resp):
        rc = cmd_mcp_submit(str(tmp_path), token="key-123")
    assert rc == 0
    out = capsys.readouterr().out
    # The follow-up command is named with the real submission id.
    assert "agentnode mcp status sub_abc123" in out
    # And it is clear the MCP is not yet public.
    assert "queued for review" in out.lower()


def test_submit_invalid_report_does_not_post(tmp_path, capsys):
    _write(tmp_path)
    report = _passing_report()
    report.status = "INVALID"
    with patch(
        "agentnode_sdk.cli.mcp_submit.verify_manifest", return_value=report,
    ), patch("agentnode_sdk.cli.mcp_submit.httpx.post") as post:
        rc = cmd_mcp_submit(str(tmp_path), token="key-123")
    assert rc == 1
    post.assert_not_called()
