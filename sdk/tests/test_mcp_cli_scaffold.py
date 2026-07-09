"""Phase 2 — MCP CLI UX: init scaffold + publish routing.

Gives MCP the same self-service entry as the other types from the terminal:
`agentnode init --type mcp` produces a schema-valid MCP listing manifest, and
`agentnode publish` on an MCP manifest routes cleanly to `agentnode mcp submit`
instead of failing deep in toolpack validation.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from agentnode_sdk.cli.init import scaffold_package
from agentnode_sdk.cli.publish import cmd_publish
from agentnode_sdk.cli.templates import TEMPLATES


def test_mcp_template_exists():
    assert "mcp" in TEMPLATES
    assert "agentnode.yaml" in TEMPLATES["mcp"]["files"]


def _scaffold_mcp(tmp_path: Path) -> dict:
    scaffold_package(
        template_key="mcp",
        target_dir=tmp_path,
        package_id="test-mcp-listing",
        name="Test MCP Listing",
        publisher="test-pub",
    )
    manifest_text = (tmp_path / "agentnode.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(manifest_text)


def test_scaffolded_mcp_manifest_is_schema_valid(tmp_path):
    """The scaffold must pass the mcp verify SCHEMA gate (runtime=mcp, mcp_server
    with a non-empty command and an npm/pypi package) — placeholders are only
    resolved later at package_exists."""
    manifest = _scaffold_mcp(tmp_path)
    assert manifest["runtime"] == "mcp"
    assert manifest["package_type"] == "toolpack"
    mcp = manifest["mcp_server"]
    assert isinstance(mcp.get("command"), list) and mcp["command"]
    assert mcp.get("npm_package") or mcp.get("pypi_package")
    assert "source_repo" in mcp

    from agentnode_sdk.cli.mcp_verify import VerifyReport, check_schema

    report = VerifyReport()
    assert check_schema(manifest, report) is True


def test_scaffold_writes_readme_pointing_to_mcp_submit(tmp_path):
    _scaffold_mcp(tmp_path)
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "agentnode mcp submit" in readme
    assert "agentnode mcp verify" in readme


def test_publish_routes_mcp_manifest_to_submit(tmp_path):
    """`agentnode publish` on an MCP manifest points to `mcp submit` and does not
    attempt the packages/publish path."""
    _scaffold_mcp(tmp_path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cmd_publish(str(tmp_path), dry_run=True)
    out = buf.getvalue()
    assert rc != 0  # did not publish here
    assert "agentnode mcp submit" in out
    assert "MCP server listing" in out


def test_publish_still_works_for_non_mcp(tmp_path):
    """A normal toolpack manifest must NOT be routed away by the MCP guard."""
    scaffold_package(
        template_key="local",
        target_dir=tmp_path,
        package_id="plain-toolpack",
        name="Plain Toolpack",
        publisher="test-pub",
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        # dry_run avoids any network; we only assert it does NOT hit the MCP route
        cmd_publish(str(tmp_path), dry_run=True, skip_validate=True, yes=True)
    out = buf.getvalue()
    assert "MCP server listing" not in out
