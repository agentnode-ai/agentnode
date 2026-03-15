"""Seed the capability_taxonomy table with 30 official IDs.
Idempotent: skips existing entries.
"""
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

CAPABILITIES = [
    ("pdf_extraction", "PDF Extraction", "document-processing"),
    ("document_parsing", "Document Parsing", "document-processing"),
    ("document_summary", "Document Summary", "document-processing"),
    ("citation_extraction", "Citation Extraction", "document-processing"),
    ("web_search", "Web Search", "web-and-browsing"),
    ("webpage_extraction", "Webpage Extraction", "web-and-browsing"),
    ("browser_navigation", "Browser Navigation", "web-and-browsing"),
    ("link_discovery", "Link Discovery", "web-and-browsing"),
    ("csv_analysis", "CSV Analysis", "data-analysis"),
    ("spreadsheet_parsing", "Spreadsheet Parsing", "data-analysis"),
    ("data_cleaning", "Data Cleaning", "data-analysis"),
    ("statistics_analysis", "Statistics Analysis", "data-analysis"),
    ("chart_generation", "Chart Generation", "data-analysis"),
    ("json_processing", "JSON Processing", "data-analysis"),
    ("sql_generation", "SQL Generation", "data-analysis"),
    ("log_analysis", "Log Analysis", "data-analysis"),
    ("vector_memory", "Vector Memory", "memory-and-retrieval"),
    ("knowledge_retrieval", "Knowledge Retrieval", "memory-and-retrieval"),
    ("semantic_search", "Semantic Search", "memory-and-retrieval"),
    ("embedding_generation", "Embedding Generation", "memory-and-retrieval"),
    ("document_indexing", "Document Indexing", "memory-and-retrieval"),
    ("conversation_memory", "Conversation Memory", "memory-and-retrieval"),
    ("email_drafting", "Email Drafting", "communication"),
    ("email_summary", "Email Summary", "communication"),
    ("meeting_summary", "Meeting Summary", "communication"),
    ("scheduling", "Scheduling", "productivity"),
    ("task_management", "Task Management", "productivity"),
    ("translation", "Translation", "language"),
    ("tone_adjustment", "Tone Adjustment", "language"),
    ("code_analysis", "Code Analysis", "development"),
]


async def seed():
    engine = create_async_engine(settings.DATABASE_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession)

    async with session_factory() as session:
        for cap_id, display_name, category in CAPABILITIES:
            await session.execute(
                text(
                    """
                    INSERT INTO capability_taxonomy (id, display_name, category)
                    VALUES (:id, :display_name, :category)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": cap_id, "display_name": display_name, "category": category},
            )
        await session.commit()
        print(f"Seeded {len(CAPABILITIES)} capabilities.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
