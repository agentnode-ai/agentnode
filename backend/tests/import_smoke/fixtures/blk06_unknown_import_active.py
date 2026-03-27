from langchain.tools import tool
from company_internal.nlp import classify_text, extract_entities


@tool
def analyze_document(text: str) -> dict:
    """Analyze a document using internal NLP tools."""
    classification = classify_text(text)
    entities = extract_entities(text)
    return {
        "classification": classification,
        "entities": entities,
        "length": len(text),
    }
