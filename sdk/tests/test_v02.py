"""Tests for ANP v0.2 SDK features — load_tool with tool_name, _resolve_entrypoint, lockfile tools."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agentnode_sdk.installer import (
    _resolve_entrypoint,
    load_tool,
    read_lockfile,
    update_lockfile,
)
from agentnode_sdk.exceptions import AgentNodeToolError


# ---------------------------------------------------------------------------
# _resolve_entrypoint tests
# ---------------------------------------------------------------------------

class TestResolveEntrypoint:
    def test_module_only_defaults_to_run(self):
        module_path, func_name = _resolve_entrypoint("my_pack.tool")
        assert module_path == "my_pack.tool"
        assert func_name == "run"

    def test_module_with_function(self):
        module_path, func_name = _resolve_entrypoint("csv_analyzer_pack.tool:describe")
        assert module_path == "csv_analyzer_pack.tool"
        assert func_name == "describe"

    def test_deep_module_path(self):
        module_path, func_name = _resolve_entrypoint("a.b.c.d:my_func")
        assert module_path == "a.b.c.d"
        assert func_name == "my_func"

    def test_explicit_run(self):
        module_path, func_name = _resolve_entrypoint("my_pack.tool:run")
        assert module_path == "my_pack.tool"
        assert func_name == "run"


# ---------------------------------------------------------------------------
# load_tool tests
# ---------------------------------------------------------------------------

class TestLoadTool:
    def _write_lockfile(self, tmp: Path, packages: dict):
        data = {"lockfile_version": "0.2", "updated_at": "", "packages": packages}
        (tmp / "agentnode.lock").write_text(json.dumps(data))

    def test_load_v01_single_tool(self, tmp_path):
        """v0.1 pack — load_tool(slug) returns module.run."""
        self._write_lockfile(tmp_path, {
            "my-pack": {
                "version": "1.0.0",
                "entrypoint": "my_pack.tool",
                "tools": [],
            }
        })

        mock_module = MagicMock()
        mock_module.run = lambda x: x

        with patch("agentnode_sdk.installer._lockfile_path", return_value=tmp_path / "agentnode.lock"):
            with patch("agentnode_sdk.installer.importlib.import_module", return_value=mock_module):
                func = load_tool("my-pack")
                assert func == mock_module.run

    def test_load_v02_with_tool_name(self, tmp_path):
        """v0.2 pack — load_tool(slug, tool_name) returns specific function."""
        self._write_lockfile(tmp_path, {
            "csv-pack": {
                "version": "1.1.0",
                "entrypoint": "csv_pack.tool",
                "tools": [
                    {"name": "describe", "entrypoint": "csv_pack.tool:describe", "capability_id": "csv_analysis"},
                    {"name": "filter", "entrypoint": "csv_pack.tool:filter_rows", "capability_id": "data_cleaning"},
                ],
            }
        })

        mock_module = MagicMock()
        mock_describe = lambda fp: {"op": "describe"}
        mock_module.describe = mock_describe

        with patch("agentnode_sdk.installer._lockfile_path", return_value=tmp_path / "agentnode.lock"):
            with patch("agentnode_sdk.installer.importlib.import_module", return_value=mock_module):
                func = load_tool("csv-pack", tool_name="describe")
                assert func == mock_describe

    def test_load_v02_tool_name_not_found(self, tmp_path):
        """load_tool with wrong tool_name raises ImportError."""
        self._write_lockfile(tmp_path, {
            "csv-pack": {
                "version": "1.1.0",
                "entrypoint": "csv_pack.tool",
                "tools": [
                    {"name": "describe", "entrypoint": "csv_pack.tool:describe", "capability_id": "csv_analysis"},
                ],
            }
        })

        with patch("agentnode_sdk.installer._lockfile_path", return_value=tmp_path / "agentnode.lock"):
            with pytest.raises(ImportError, match="nonexistent"):
                load_tool("csv-pack", tool_name="nonexistent")

    def test_load_not_installed(self, tmp_path):
        """load_tool for uninstalled package raises ImportError."""
        self._write_lockfile(tmp_path, {})

        with patch("agentnode_sdk.installer._lockfile_path", return_value=tmp_path / "agentnode.lock"):
            with pytest.raises(ImportError, match="not installed"):
                load_tool("missing-pack")

    def test_load_no_entrypoint(self, tmp_path):
        """load_tool for pack with no entrypoint raises ImportError."""
        self._write_lockfile(tmp_path, {
            "broken-pack": {
                "version": "1.0.0",
                "entrypoint": "",
                "tools": [],
            }
        })

        with patch("agentnode_sdk.installer._lockfile_path", return_value=tmp_path / "agentnode.lock"):
            with pytest.raises(ImportError, match="no entrypoint"):
                load_tool("broken-pack")


# ---------------------------------------------------------------------------
# Lockfile tools array tests
# ---------------------------------------------------------------------------

class TestLockfileTools:
    def test_lockfile_stores_tools(self, tmp_path):
        """update_lockfile should store tools array."""
        lf_path = tmp_path / "agentnode.lock"
        entry = {
            "version": "1.1.0",
            "entrypoint": "csv_pack.tool",
            "tools": [
                {"name": "describe", "entrypoint": "csv_pack.tool:describe", "capability_id": "csv_analysis"},
                {"name": "filter", "entrypoint": "csv_pack.tool:filter_rows", "capability_id": "data_cleaning"},
            ],
        }

        with patch("agentnode_sdk.installer._lockfile_path", return_value=lf_path):
            update_lockfile("csv-pack", entry, path=lf_path)

        data = json.loads(lf_path.read_text())
        assert "csv-pack" in data["packages"]
        tools = data["packages"]["csv-pack"]["tools"]
        assert len(tools) == 2
        assert tools[0]["name"] == "describe"
        assert tools[1]["entrypoint"] == "csv_pack.tool:filter_rows"

    def test_lockfile_backward_compat_no_tools(self, tmp_path):
        """Old lockfile entries without tools should still work."""
        lf_path = tmp_path / "agentnode.lock"
        data = {
            "lockfile_version": "0.1",
            "updated_at": "",
            "packages": {
                "old-pack": {
                    "version": "1.0.0",
                    "entrypoint": "old_pack.tool",
                    # No "tools" key at all
                }
            }
        }
        lf_path.write_text(json.dumps(data))

        with patch("agentnode_sdk.installer._lockfile_path", return_value=lf_path):
            result = read_lockfile(lf_path)
            old_pack = result["packages"]["old-pack"]
            # Should not crash; tools defaults to not existing
            assert old_pack.get("tools", []) == []


# ---------------------------------------------------------------------------
# AgentNodeToolError tests
# ---------------------------------------------------------------------------

class TestAgentNodeToolError:
    def test_basic_error(self):
        err = AgentNodeToolError("Something went wrong")
        assert str(err) == "Something went wrong"
        assert err.tool_name is None
        assert err.details == {}

    def test_error_with_tool_name(self):
        err = AgentNodeToolError("File not found", tool_name="describe_csv")
        assert err.tool_name == "describe_csv"

    def test_error_with_details(self):
        err = AgentNodeToolError("Bad input", details={"field": "file_path"})
        assert err.details == {"field": "file_path"}

    def test_is_exception(self):
        """AgentNodeToolError should be catchable as Exception."""
        with pytest.raises(AgentNodeToolError):
            raise AgentNodeToolError("test")

    def test_not_agentnode_error(self):
        """AgentNodeToolError is NOT a subclass of AgentNodeError (different hierarchy)."""
        from agentnode_sdk.exceptions import AgentNodeError
        assert not issubclass(AgentNodeToolError, AgentNodeError)
