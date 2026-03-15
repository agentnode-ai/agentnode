"""Tests for the LangChain adapter."""
from unittest.mock import MagicMock, patch

from agentnode_langchain.tools import AgentNodeTool, _json_schema_to_pydantic


def test_agentnode_tool_run():
    tool = AgentNodeTool(
        name="extract_pdf",
        description="Extract text from PDF",
        capability_id="pdf_extraction",
        package_slug="pdf-reader",
    )
    result = tool._run(input="test.pdf")
    assert "pdf-reader" in result
    assert "extract_pdf" in result
    assert "pdf_extraction" in result


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
