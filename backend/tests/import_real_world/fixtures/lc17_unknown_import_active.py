from langchain.tools import tool
from company_internal.nlp import sentiment_analyzer, entity_extractor


@tool
def analyze_sentiment(text: str) -> dict:
    """Analyze the sentiment of the given text."""
    score = sentiment_analyzer.predict(text)
    entities = entity_extractor.extract(text)
    return {"sentiment": score, "entities": entities}
