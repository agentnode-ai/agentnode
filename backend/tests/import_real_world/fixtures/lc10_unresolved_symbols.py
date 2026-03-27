from langchain.tools import tool


@tool
def enrich_data(record: str) -> dict:
    """Enrich a data record with external information."""
    parsed = parse_record(record)
    enriched = lookup_external_data(parsed["id"])
    score = calculate_quality_score(enriched)
    return {"enriched": enriched, "quality_score": score}
