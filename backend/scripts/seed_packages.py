"""Seed additional packages into the registry using raw SQL."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine
from app.shared.meili import sync_package_to_meilisearch
from sqlalchemy import text

PACKS = [
    {
        "slug": "csv-analyzer-pack",
        "name": "CSV Analyzer Pack",
        "summary": "Analyze, filter, and transform CSV data with pandas-powered tools.",
        "description": "Wraps pandas to provide CSV ingestion, column stats, filtering, and export for AI agents.",
        "capabilities": [{"name": "analyze_csv", "capability_id": "csv_analysis", "type": "tool"}],
        "entrypoint": "csv_analyzer_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "email-drafter-pack",
        "name": "Email Drafter Pack",
        "summary": "Draft professional emails from bullet points or intent descriptions.",
        "description": "Uses templates and LLM prompts to generate well-structured emails.",
        "capabilities": [{"name": "draft_email", "capability_id": "email_drafting", "type": "tool"}],
        "entrypoint": "email_drafter_pack.tool",
        "permissions": {"network": "none", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "document-summarizer-pack",
        "name": "Document Summarizer Pack",
        "summary": "Summarize long documents into concise, structured text.",
        "description": "Extractive and abstractive summarization for PDFs, text files, and HTML content.",
        "capabilities": [{"name": "summarize_document", "capability_id": "document_summary", "type": "tool"}],
        "entrypoint": "document_summarizer_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "sql-generator-pack",
        "name": "SQL Generator Pack",
        "summary": "Generate SQL queries from natural language descriptions.",
        "description": "Translates natural language to SQL with schema awareness and query validation.",
        "capabilities": [{"name": "generate_sql", "capability_id": "sql_generation", "type": "tool"}],
        "entrypoint": "sql_generator_pack.tool",
        "permissions": {"network": "none", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "once"},
    },
    {
        "slug": "text-translator-pack",
        "name": "Text Translator Pack",
        "summary": "Translate text between 50+ languages with automatic language detection.",
        "description": "Wraps deep-translator for reliable multi-language text translation.",
        "capabilities": [{"name": "translate_text", "capability_id": "text_translation", "type": "tool"}],
        "entrypoint": "text_translator_pack.tool",
        "permissions": {"network": "restricted", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "image-analyzer-pack",
        "name": "Image Analyzer Pack",
        "summary": "Analyze and describe images using vision models.",
        "description": "Extract objects, text, colors, and scene descriptions from images.",
        "capabilities": [{"name": "analyze_image", "capability_id": "image_analysis", "type": "tool"}],
        "entrypoint": "image_analyzer_pack.tool",
        "permissions": {"network": "restricted", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "code-executor-pack",
        "name": "Code Executor Pack",
        "summary": "Execute Python code in a secure sandboxed environment.",
        "description": "Provides safe code execution with timeout, memory limits, and output capture.",
        "capabilities": [{"name": "execute_code", "capability_id": "code_execution", "type": "tool"}],
        "entrypoint": "code_executor_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "limited_subprocess", "data_access": "input_only", "user_approval": "always"},
    },
    {
        "slug": "api-connector-pack",
        "name": "API Connector Pack",
        "summary": "Connect to external REST APIs with authentication and retry logic.",
        "description": "Universal REST API client with OAuth, API key, and bearer token support.",
        "capabilities": [{"name": "call_api", "capability_id": "api_integration", "type": "tool"}],
        "entrypoint": "api_connector_pack.tool",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "once"},
    },
    {
        "slug": "semantic-search-pack",
        "name": "Semantic Search Pack",
        "summary": "Perform semantic similarity search over document collections.",
        "description": "Embeds queries and documents for cosine similarity search with FAISS.",
        "capabilities": [{"name": "semantic_search", "capability_id": "semantic_search", "type": "tool"}],
        "entrypoint": "semantic_search_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "json-processor-pack",
        "name": "JSON Processor Pack",
        "summary": "Parse, transform, query, and validate JSON data structures.",
        "description": "JMESPath queries, schema validation, and structural transformations for JSON.",
        "capabilities": [{"name": "process_json", "capability_id": "json_processing", "type": "tool"}],
        "entrypoint": "json_processor_pack.tool",
        "permissions": {"network": "none", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "scheduler-pack",
        "name": "Scheduler Pack",
        "summary": "Schedule tasks, manage calendars, and set reminders.",
        "description": "Calendar integration with iCal support and cron-style scheduling.",
        "capabilities": [{"name": "schedule_task", "capability_id": "scheduling", "type": "tool"}],
        "entrypoint": "scheduler_pack.tool",
        "permissions": {"network": "restricted", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "once"},
    },
    {
        "slug": "embedding-generator-pack",
        "name": "Embedding Generator Pack",
        "summary": "Generate vector embeddings from text using sentence transformers.",
        "description": "Creates dense vector representations for semantic similarity and retrieval.",
        "capabilities": [{"name": "generate_embedding", "capability_id": "embedding_generation", "type": "tool"}],
        "entrypoint": "embedding_generator_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
]


async def seed():
    async with engine.begin() as conn:
        # Get publisher ID
        result = await conn.execute(text("SELECT id FROM publishers WHERE slug = 'agentnode'"))
        publisher_id = result.scalar()
        if not publisher_id:
            print("ERROR: Publisher 'agentnode' not found")
            return

        for pack in PACKS:
            # Check if exists
            result = await conn.execute(text("SELECT id FROM packages WHERE slug = :slug"), {"slug": pack["slug"]})
            if result.scalar():
                print(f"SKIP {pack['slug']} (exists)")
                continue

            # Create package
            result = await conn.execute(text(
                "INSERT INTO packages "
                "(publisher_id, slug, name, package_type, summary, description) "
                "VALUES (:pub_id, :slug, :name, 'toolpack', :summary, :desc) "
                "RETURNING id"
            ), {
                "pub_id": publisher_id, "slug": pack["slug"],
                "name": pack["name"], "summary": pack["summary"],
                "desc": pack["description"],
            })
            pkg_id = result.scalar()

            # Create version
            manifest_raw = {"package_id": pack["slug"], "version": "1.0.0", "name": pack["name"]}
            result = await conn.execute(text(
                "INSERT INTO package_versions "
                "(package_id, version_number, channel, manifest_raw, runtime, "
                "install_mode, hosting_type, entrypoint, quarantine_status) "
                "VALUES (:pkg_id, '1.0.0', 'stable', :manifest, 'python', "
                "'package', 'agentnode_hosted', :ep, 'cleared') "
                "RETURNING id"
            ), {
                "pkg_id": pkg_id, "manifest": json.dumps(manifest_raw),
                "ep": pack["entrypoint"],
            })
            version_id = result.scalar()

            # Set latest_version_id
            await conn.execute(text(
                "UPDATE packages SET latest_version_id = :vid WHERE id = :pid"
            ), {"vid": version_id, "pid": pkg_id})

            # Capabilities
            for cap in pack["capabilities"]:
                await conn.execute(text(
                    "INSERT INTO capabilities "
                    "(package_version_id, capability_type, capability_id, name, description) "
                    "VALUES (:vid, :ctype, :cid, :name, :desc)"
                ), {
                    "vid": version_id, "ctype": cap["type"],
                    "cid": cap["capability_id"], "name": cap["name"],
                    "desc": cap["name"],
                })

            # Permissions
            p = pack["permissions"]
            await conn.execute(text(
                "INSERT INTO permissions "
                "(package_version_id, network_level, filesystem_level, "
                "code_execution_level, data_access_level, user_approval_level) "
                "VALUES (:vid, :net, :fs, :exec, :data, :approval)"
            ), {
                "vid": version_id, "net": p["network"], "fs": p["filesystem"],
                "exec": p["code_execution"], "data": p["data_access"],
                "approval": p["user_approval"],
            })

            # Compatibility rules
            for fw in ["generic"]:
                await conn.execute(text(
                    "INSERT INTO compatibility_rules "
                    "(package_version_id, framework, runtime_version) "
                    "VALUES (:vid, :fw, '>=3.10')"
                ), {"vid": version_id, "fw": fw})

            # Sync to Meilisearch
            cap_ids = [c["capability_id"] for c in pack["capabilities"]]
            meili_doc = {
                "slug": pack["slug"],
                "name": pack["name"],
                "package_type": "toolpack",
                "summary": pack["summary"],
                "description": pack["description"],
                "publisher_name": "agentnode",
                "publisher_slug": "agentnode",
                "trust_level": "trusted",
                "latest_version": "1.0.0",
                "runtime": "python",
                "capability_ids": cap_ids,
                "tags": [],
                "frameworks": ["generic"],
                "download_count": 0,
                "is_deprecated": False,
            }
            try:
                await sync_package_to_meilisearch(meili_doc)
            except Exception as e:
                print(f"  Meili sync failed: {e}")

            print(f"OK {pack['slug']} v1.0.0")

    await engine.dispose()
    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(seed())
