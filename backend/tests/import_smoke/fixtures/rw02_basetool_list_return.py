"""Real-world: BaseTool subclass with List[str] return type (not dict).
Source: github.com/langchain-ai/langchain/issues/13602
"""
from langchain.tools import BaseTool
from typing import List


class ListTool(BaseTool):
    name = "List Tool"
    description = "Generates a list of strings."

    def _run(self) -> List[str]:
        """Return a list of strings."""
        return ["apple", "banana", "cherry"]


class ProcessListTool(BaseTool):
    name = "Process List Tool"
    description = "Processes a list of strings."

    def _run(self, input_list: List[str]) -> str:
        """Process the list of strings."""
        processed_list = [item.upper() for item in input_list]
        return f"Processed list: {', '.join(processed_list)}"
