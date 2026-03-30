"""Tests for agentnode_crewai adapter."""
from unittest.mock import MagicMock, patch

import pytest

from agentnode_crewai.tools import AgentNodeTool, get_crewai_tools, _build_args_schema


# ---------------------------------------------------------------------------
# _build_args_schema
# ---------------------------------------------------------------------------

def test_build_args_schema_empty():
    model = _build_args_schema({})
    assert model.__name__ == "EmptyInput"


def test_build_args_schema_with_properties():
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
        },
        "required": ["query"],
    }
    model = _build_args_schema(schema)
    assert "query" in model.model_fields


# ---------------------------------------------------------------------------
# get_crewai_tools
# ---------------------------------------------------------------------------

@patch("agentnode_crewai.tools.AgentNodeRuntime")
def test_get_tools_returns_list(mock_runtime_cls):
    runtime = MagicMock()
    runtime.as_generic_tools.return_value = [
        {"name": "tool_a", "description": "A", "input_schema": {}},
        {"name": "tool_b", "description": "B", "input_schema": {}},
    ]
    mock_runtime_cls.return_value = runtime

    tools = get_crewai_tools()

    assert isinstance(tools, list)
    assert len(tools) == 2
    assert all(isinstance(t, AgentNodeTool) for t in tools)


@patch("agentnode_crewai.tools.AgentNodeRuntime")
def test_tool_has_name_and_description(mock_runtime_cls):
    runtime = MagicMock()
    runtime.as_generic_tools.return_value = [
        {"name": "agentnode_search", "description": "Search stuff", "input_schema": {}},
    ]
    mock_runtime_cls.return_value = runtime

    tools = get_crewai_tools()

    assert tools[0].name == "agentnode_search"
    # CrewAI wraps description with tool metadata; check our text is included
    assert "Search stuff" in tools[0].description


# ---------------------------------------------------------------------------
# AgentNodeTool._run
# ---------------------------------------------------------------------------

@patch("agentnode_crewai.tools.AgentNodeRuntime")
def test_tool_run_calls_runtime_handle(mock_runtime_cls):
    runtime = MagicMock()
    runtime.as_generic_tools.return_value = [
        {
            "name": "agentnode_search",
            "description": "Search",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "q"},
                },
                "required": ["query"],
            },
        },
    ]
    runtime.handle.return_value = {"success": True, "result": {"total": 1}}
    mock_runtime_cls.return_value = runtime

    tools = get_crewai_tools()
    result = tools[0]._run(query="pdf reader")

    runtime.handle.assert_called_once_with("agentnode_search", {"query": "pdf reader"})
    assert '"success": true' in result


@patch("agentnode_crewai.tools.AgentNodeRuntime")
def test_tool_run_catches_exceptions(mock_runtime_cls):
    runtime = MagicMock()
    runtime.as_generic_tools.return_value = [
        {"name": "boom", "description": "Explodes", "input_schema": {}},
    ]
    runtime.handle.side_effect = RuntimeError("kaboom")
    mock_runtime_cls.return_value = runtime

    tools = get_crewai_tools()
    result = tools[0]._run()

    assert '"success": false' in result
    assert "kaboom" in result
