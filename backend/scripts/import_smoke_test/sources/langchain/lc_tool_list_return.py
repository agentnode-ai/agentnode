"""
@tool function with typed List[dict] return annotation.
Less common but seen in more carefully typed codebases.
"""

import csv
import io
from typing import List

import requests
from langchain.tools import tool


@tool
def fetch_csv_as_records(url: str, max_rows: int = 100) -> List[dict]:
    """
    Fetch a CSV file from a URL and return it as a list of dicts.

    Each row in the CSV becomes a dict keyed by column header.

    Args:
        url: Direct URL to a CSV file
        max_rows: Maximum number of rows to return (default 100)

    Returns:
        List of dicts, one per CSV row
    """
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        content = resp.text

        reader = csv.DictReader(io.StringIO(content))
        rows = []
        for i, row in enumerate(reader):
            if i >= max_rows:
                break
            rows.append(dict(row))

        return rows
    except requests.RequestException as e:
        return [{"error": str(e), "url": url}]
    except csv.Error as e:
        return [{"error": f"CSV parse error: {e}", "url": url}]
    except Exception as e:
        return [{"error": str(e), "url": url}]
