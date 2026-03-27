from langchain.tools import tool


@tool
def analyze_data(data: str) -> dict:
    """Analyze input data using an external processor."""
    processed = external_processor(data)
    return {"analysis": processed, "original_length": len(data)}
