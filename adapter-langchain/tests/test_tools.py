"""Tests for the LangChain adapter."""
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools.base import ToolException

from agentnode_langchain.loader import AgentNodeTool, _json_schema_to_pydantic


def test_agentnode_tool_run_no_entrypoint():
    """Tool without entrypoint raises ToolException."""
    tool = AgentNodeTool(
        name="extract_pdf",
        description="Extract text from PDF",
        capability_id="pdf_extraction",
        package_slug="pdf-reader",
    )
    with pytest.raises(ToolException, match="No entrypoint configured"):
        tool._run(input="test.pdf")


def test_agentnode_tool_run_with_entrypoint():
    """Tool with entrypoint imports module and calls function."""
    tool = AgentNodeTool(
        name="extract_pdf",
        description="Extract text from PDF",
        capability_id="pdf_extraction",
        package_slug="pdf-reader",
        entrypoint="pdf_reader.tool:extract",
    )
    mock_module = MagicMock()
    mock_module.extract.return_value = "extracted text"
    with patch("importlib.import_module", return_value=mock_module):
        result = tool._run(input="test.pdf")
    assert result == "extracted text"
    mock_module.extract.assert_called_once_with(input="test.pdf")


def test_json_schema_to_pydantic():
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "depth": {"type": "integer"},
        },
        "required": ["url"],
    }
    Model = _json_schema_to_pydantic("TestTool", schema)
    instance = Model(url="https://example.com")
    assert instance.url == "https://example.com"
    assert instance.depth is None


def test_json_schema_empty():
    Model = _json_schema_to_pydantic("Empty", None)
    instance = Model()
    assert instance is not None
