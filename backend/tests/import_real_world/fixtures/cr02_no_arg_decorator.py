from crewai_tools import tool


@tool
def csv_stats(file_path: str) -> dict:
    """Calculate basic statistics for a CSV file."""
    import pandas as pd
    df = pd.read_csv(file_path)
    return {
        "rows": len(df),
        "columns": list(df.columns),
        "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
        "null_counts": df.isnull().sum().to_dict(),
    }
