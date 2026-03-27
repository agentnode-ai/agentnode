from crewai_tools import tool
import json


def _clean_json(raw: str) -> dict:
    """Parse and normalize a JSON string."""
    data = json.loads(raw)
    return {k.lower().strip(): v for k, v in data.items()}


@tool("JSON Cleaner")
def clean_json(raw_json: str) -> dict:
    """Parse, clean, and normalize a raw JSON string."""
    try:
        cleaned = _clean_json(raw_json)
        return {"cleaned": cleaned, "key_count": len(cleaned)}
    except json.JSONDecodeError as e:
        return {"error": str(e), "key_count": 0}
