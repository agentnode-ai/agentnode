from langchain.tools import tool
import company_metrics


@tool
def format_report(title: str, body: str) -> dict:
    """Format a simple text report with title and body."""
    formatted = f"# {title}\n\n{body}"
    return {"report": formatted, "length": len(formatted)}
