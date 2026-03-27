"""
File I/O tool using crewAI @tool decorator.
Returns str — common in text-pipeline tasks where agents read/write files.
"""

import os
from pathlib import Path

from crewai_tools import tool


@tool("File Manager")
def manage_file(operation: str, path: str, content: str = "") -> str:
    """
    Read, write, or append to a text file.

    Args:
        operation: One of 'read', 'write', 'append', 'exists', 'delete'
        path: Path to the file
        content: Content to write or append (only used for write/append)

    Returns:
        String with operation result or file contents
    """
    file_path = Path(path)

    if operation == "read":
        if not file_path.exists():
            return f"Error: file not found at {path}"
        try:
            return file_path.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}"

    elif operation == "write":
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} characters to {path}"
        except Exception as e:
            return f"Error writing file: {e}"

    elif operation == "append":
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully appended {len(content)} characters to {path}"
        except Exception as e:
            return f"Error appending to file: {e}"

    elif operation == "exists":
        return f"File {'exists' if file_path.exists() else 'does not exist'}: {path}"

    elif operation == "delete":
        if not file_path.exists():
            return f"File not found: {path}"
        try:
            file_path.unlink()
            return f"Successfully deleted {path}"
        except Exception as e:
            return f"Error deleting file: {e}"

    else:
        return f"Unknown operation: {operation}. Use: read, write, append, exists, delete"
