"""Capability complement graph — shared between doctor and recommend."""
from __future__ import annotations

CAPABILITY_COMPLEMENTS: dict[str, list[str]] = {
    "web_search": ["text_summarization", "webpage_extraction", "knowledge_graph"],
    "webpage_extraction": ["web_search", "text_summarization", "pdf_extraction"],
    "pdf_extraction": ["text_summarization", "document_parsing", "ocr_reading"],
    "csv_analysis": ["chart_generation", "data_visualization", "spreadsheet_parsing"],
    "text_summarization": ["text_translation", "web_search", "pdf_extraction"],
    "text_translation": ["text_summarization", "language_detection"],
    "browser_navigation": ["webpage_extraction", "web_search", "screenshot_capture"],
    "embedding_generation": ["vector_memory", "text_summarization"],
    "vector_memory": ["embedding_generation", "web_search"],
    "sql_generation": ["csv_analysis", "database_connector"],
    "chart_generation": ["csv_analysis", "data_visualization"],
    "code_analysis": ["code_generation", "test_generation"],
    "code_generation": ["code_analysis", "test_generation"],
    "ocr_reading": ["pdf_extraction", "text_summarization"],
}
