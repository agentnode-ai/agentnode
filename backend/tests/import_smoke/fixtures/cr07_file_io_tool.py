from crewai_tools import tool


@tool("Line Counter")
def count_lines(file_path: str) -> dict:
    """Count lines, blank lines, and non-blank lines in a text file."""
    with open(file_path, encoding="utf-8") as f:
        lines = f.readlines()
    total = len(lines)
    blank = sum(1 for line in lines if not line.strip())
    return {"total_lines": total, "blank_lines": blank, "content_lines": total - blank}
