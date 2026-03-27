from langchain.tools import tool


@tool
def process_data(data, format="json", verbose=False) -> dict:
    """Process data in the specified format."""
    if format == "json":
        import json
        parsed = json.loads(data) if isinstance(data, str) else data
    else:
        parsed = str(data)
    return {"processed": parsed, "format": format}
