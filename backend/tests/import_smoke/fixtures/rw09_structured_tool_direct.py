"""Real-world: StructuredTool direct instantiation (not from_function).
Source: github.com/langchain-ai/langchain/issues/34900
"""
from langchain_core.tools import StructuredTool


def func(**kwargs) -> str:
    """Process arbitrary keyword arguments."""
    return f"{kwargs}"


tool = StructuredTool(
    name="test_tool",
    func=func,
    description="A test tool that accepts arbitrary inputs",
    args_schema={
        "type": "object",
        "properties": {},
    },
)
