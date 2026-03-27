"""
File reader tool using BaseTool — returns str, not dict.
Common in older LangChain tutorials before structured outputs were preferred.
"""

import os
from typing import Type

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class FileReadInput(BaseModel):
    path: str = Field(..., description="Path to the file to read")
    encoding: str = Field(default="utf-8", description="File encoding")


class FileReaderTool(BaseTool):
    name: str = "file_reader"
    description: str = (
        "Read a text file from disk and return its contents as a string. "
        "Use this when you need to examine a local file."
    )
    args_schema: Type[BaseModel] = FileReadInput

    def _run(self, path: str, encoding: str = "utf-8") -> str:
        """Read file and return raw string content."""
        if not os.path.isfile(path):
            return f"Error: file not found at path '{path}'"

        try:
            with open(path, encoding=encoding) as f:
                content = f.read()
            return content
        except PermissionError:
            return f"Error: permission denied reading '{path}'"
        except UnicodeDecodeError:
            return f"Error: could not decode file with encoding '{encoding}'"
        except Exception as e:
            return f"Error: {e}"

    async def _arun(self, path: str, encoding: str = "utf-8") -> str:
        raise NotImplementedError("Async not supported")
