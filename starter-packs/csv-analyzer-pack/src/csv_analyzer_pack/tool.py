"""CSV analysis tool using pandas."""

from __future__ import annotations

import pandas as pd


def run(file_path: str, operation: str = "describe", **kwargs) -> dict:
    """Analyze a CSV file.

    Args:
        file_path: Path to the CSV file.
        operation: One of "describe", "head", "columns", "filter".
        **kwargs: Extra arguments for specific operations.
            For "head": n (int) - number of rows, default 5.
            For "filter": column (str), value - filter rows where column == value.
                          operator (str) - one of "==", "!=", ">", "<", ">=", "<=".

    Returns:
        A dict with the operation result.
    """
    df = pd.read_csv(file_path)

    if operation == "describe":
        stats = df.describe(include="all").to_dict()
        return {
            "operation": "describe",
            "rows": len(df),
            "columns": len(df.columns),
            "statistics": stats,
        }

    elif operation == "head":
        n = kwargs.get("n", 5)
        rows = df.head(int(n)).to_dict(orient="records")
        return {
            "operation": "head",
            "n": n,
            "total_rows": len(df),
            "rows": rows,
        }

    elif operation == "columns":
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

    elif operation == "filter":
        column = kwargs.get("column")
        value = kwargs.get("value")
        operator = kwargs.get("operator", "==")

        if column is None or column not in df.columns:
            return {
                "operation": "filter",
                "error": f"Column '{column}' not found. Available: {list(df.columns)}",
            }

        if operator == "==":
            mask = df[column] == value
        elif operator == "!=":
            mask = df[column] != value
        elif operator == ">":
            mask = df[column] > value
        elif operator == "<":
            mask = df[column] < value
        elif operator == ">=":
            mask = df[column] >= value
        elif operator == "<=":
            mask = df[column] <= value
        else:
            return {
                "operation": "filter",
                "error": f"Unknown operator '{operator}'. Use ==, !=, >, <, >=, <=.",
            }

        filtered = df[mask]
        return {
            "operation": "filter",
            "column": column,
            "operator": operator,
            "value": value,
            "matched_rows": len(filtered),
            "total_rows": len(df),
            "rows": filtered.to_dict(orient="records"),
        }

    else:
        return {
            "error": f"Unknown operation '{operation}'. Use describe, head, columns, or filter.",
        }
