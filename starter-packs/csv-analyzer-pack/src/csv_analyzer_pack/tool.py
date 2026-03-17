"""CSV analysis tool using pandas. ANP v0.2 — per-tool entrypoints."""

from __future__ import annotations

import pandas as pd

from agentnode_sdk.exceptions import AgentNodeToolError


def describe(file_path: str) -> dict:
    """Return summary statistics for a CSV file.

    Args:
        file_path: Path to the CSV file.

    Returns:
        A dict with row count, column count, and descriptive statistics.
    """
    try:
        df = pd.read_csv(file_path)
    except FileNotFoundError:
        raise AgentNodeToolError(f"File not found: {file_path}", tool_name="describe_csv")
    except Exception as e:
        raise AgentNodeToolError(f"Failed to read CSV: {e}", tool_name="describe_csv")

    stats = df.describe(include="all").to_dict()
    return {
        "operation": "describe",
        "rows": len(df),
        "columns": len(df.columns),
        "statistics": stats,
    }


def head(file_path: str, n: int = 5) -> dict:
    """Return the first N rows of a CSV file.

    Args:
        file_path: Path to the CSV file.
        n: Number of rows to return (default 5).

    Returns:
        A dict with the first N rows as records.
    """
    try:
        df = pd.read_csv(file_path)
    except FileNotFoundError:
        raise AgentNodeToolError(f"File not found: {file_path}", tool_name="head_csv")
    except Exception as e:
        raise AgentNodeToolError(f"Failed to read CSV: {e}", tool_name="head_csv")

    rows = df.head(int(n)).to_dict(orient="records")
    return {
        "operation": "head",
        "n": n,
        "total_rows": len(df),
        "rows": rows,
    }


def columns(file_path: str) -> dict:
    """Return detailed column information for a CSV file.

    Args:
        file_path: Path to the CSV file.

    Returns:
        A dict with column names, types, null counts, and numeric stats.
    """
    try:
        df = pd.read_csv(file_path)
    except FileNotFoundError:
        raise AgentNodeToolError(f"File not found: {file_path}", tool_name="columns_csv")
    except Exception as e:
        raise AgentNodeToolError(f"Failed to read CSV: {e}", tool_name="columns_csv")

    col_info = []
    for col in df.columns:
        info = {
            "name": col,
            "dtype": str(df[col].dtype),
            "non_null_count": int(df[col].notna().sum()),
            "null_count": int(df[col].isna().sum()),
            "unique_count": int(df[col].nunique()),
        }
        if pd.api.types.is_numeric_dtype(df[col]):
            info["min"] = float(df[col].min()) if df[col].notna().any() else None
            info["max"] = float(df[col].max()) if df[col].notna().any() else None
            info["mean"] = float(df[col].mean()) if df[col].notna().any() else None
        col_info.append(info)
    return {
        "operation": "columns",
        "total_columns": len(df.columns),
        "column_info": col_info,
    }


def filter_rows(file_path: str, column: str, value: str, operator: str = "==") -> dict:
    """Filter rows by a column condition.

    Args:
        file_path: Path to the CSV file.
        column: Column name to filter on.
        value: Value to compare against.
        operator: Comparison operator — one of "==", "!=", ">", "<", ">=", "<=".

    Returns:
        A dict with matched rows and count.
    """
    try:
        df = pd.read_csv(file_path)
    except FileNotFoundError:
        raise AgentNodeToolError(f"File not found: {file_path}", tool_name="filter_csv")
    except Exception as e:
        raise AgentNodeToolError(f"Failed to read CSV: {e}", tool_name="filter_csv")

    if column not in df.columns:
        raise AgentNodeToolError(
            f"Column '{column}' not found. Available: {list(df.columns)}",
            tool_name="filter_csv",
        )

    ops = {
        "==": lambda: df[column] == value,
        "!=": lambda: df[column] != value,
        ">": lambda: df[column] > value,
        "<": lambda: df[column] < value,
        ">=": lambda: df[column] >= value,
        "<=": lambda: df[column] <= value,
    }

    if operator not in ops:
        raise AgentNodeToolError(
            f"Unknown operator '{operator}'. Use ==, !=, >, <, >=, <=.",
            tool_name="filter_csv",
        )

    filtered = df[ops[operator]()]
    return {
        "operation": "filter",
        "column": column,
        "operator": operator,
        "value": value,
        "matched_rows": len(filtered),
        "total_rows": len(df),
        "rows": filtered.to_dict(orient="records"),
    }


# Backward-compatible v0.1 entrypoint
def run(file_path: str, operation: str = "describe", **kwargs) -> dict:
    """Analyze a CSV file (v0.1 compatibility wrapper).

    Args:
        file_path: Path to the CSV file.
        operation: One of "describe", "head", "columns", "filter".
        **kwargs: Extra arguments for specific operations.
    """
    dispatch = {
        "describe": lambda: describe(file_path),
        "head": lambda: head(file_path, n=kwargs.get("n", 5)),
        "columns": lambda: columns(file_path),
        "filter": lambda: filter_rows(
            file_path,
            column=kwargs.get("column", ""),
            value=kwargs.get("value", ""),
            operator=kwargs.get("operator", "=="),
        ),
    }
    handler = dispatch.get(operation)
    if not handler:
        raise AgentNodeToolError(
            f"Unknown operation '{operation}'. Use describe, head, columns, or filter.",
            tool_name="csv_analysis",
        )
    return handler()
