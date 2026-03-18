"""Seed packages inspired by Skills.sh most popular skills."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine
from app.shared.meili import sync_package_to_meilisearch
from sqlalchemy import text

TAXONOMY_ENTRIES = [
    # Document Office Suite
    {"id": "powerpoint_generation", "display_name": "PowerPoint Generation", "description": "Create and edit PowerPoint presentations", "category": "document-processing"},
    {"id": "word_document", "display_name": "Word Document Processing", "description": "Create and edit Word documents", "category": "document-processing"},
    {"id": "excel_processing", "display_name": "Excel Processing", "description": "Create and manipulate Excel spreadsheets", "category": "data-processing"},
    # Video & Media
    {"id": "video_generation", "display_name": "Video Generation", "description": "Generate videos from text or templates", "category": "vision"},
    {"id": "audio_processing", "display_name": "Audio Processing", "description": "Process and edit audio files", "category": "language"},
    {"id": "gif_creation", "display_name": "GIF Creation", "description": "Create animated GIFs", "category": "vision"},
    # Marketing & Content
    {"id": "seo_optimization", "display_name": "SEO Optimization", "description": "Analyze and optimize content for search engines", "category": "search"},
    {"id": "copywriting", "display_name": "Copywriting", "description": "Generate marketing copy and content", "category": "language"},
    {"id": "social_media", "display_name": "Social Media Management", "description": "Manage social media posts and analytics", "category": "communication"},
    # Security & Compliance
    {"id": "security_audit", "display_name": "Security Audit", "description": "Audit code for security vulnerabilities", "category": "developer-tools"},
    {"id": "secret_scanning", "display_name": "Secret Scanning", "description": "Detect hardcoded secrets and credentials", "category": "developer-tools"},
    {"id": "contract_review", "display_name": "Contract Review", "description": "Analyze legal contracts and documents", "category": "document-processing"},
    # Code Quality
    {"id": "code_linting", "display_name": "Code Linting", "description": "Enforce code style and catch bugs", "category": "developer-tools"},
    {"id": "code_refactoring", "display_name": "Code Refactoring", "description": "Refactor and improve code quality", "category": "developer-tools"},
    {"id": "api_documentation", "display_name": "API Documentation", "description": "Generate API documentation", "category": "developer-tools"},
    {"id": "test_generation", "display_name": "Test Generation", "description": "Generate unit and integration tests", "category": "developer-tools"},
    # Design & Frontend
    {"id": "web_design", "display_name": "Web Design", "description": "Generate web design guidelines and assets", "category": "web-and-browsing"},
    {"id": "icon_generation", "display_name": "Icon Generation", "description": "Generate favicons, app icons, and assets", "category": "vision"},
    # Prompting & AI
    {"id": "prompt_engineering", "display_name": "Prompt Engineering", "description": "Design and optimize AI prompts", "category": "language"},
    # Data Science
    {"id": "scientific_computing", "display_name": "Scientific Computing", "description": "Perform scientific calculations and analysis", "category": "data-analysis"},
    # Workflow
    {"id": "user_story_planning", "display_name": "User Story Planning", "description": "Break features into user stories", "category": "productivity"},
    {"id": "document_redaction", "display_name": "Document Redaction", "description": "Redact sensitive information from documents", "category": "document-processing"},
    {"id": "citation_management", "display_name": "Citation Management", "description": "Format and manage citations", "category": "document-processing"},
    # Cloud
    {"id": "azure_integration", "display_name": "Azure Integration", "description": "Manage Azure cloud resources", "category": "integration"},
]

PACKS = [
    # --- Office Document Suite (PowerPoint 35K, Word 31K, Excel 28K) ---
    {
        "slug": "powerpoint-generator-pack",
        "name": "PowerPoint Generator Pack",
        "summary": "Create professional PowerPoint presentations from text or outlines.",
        "description": "Generate slide decks with layouts, charts, images, and formatting. Supports templates, speaker notes, and export to PPTX. Uses python-pptx.",
        "capabilities": [{"name": "generate_pptx", "capability_id": "powerpoint_generation", "type": "tool"}],
        "entrypoint": "powerpoint_generator_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "word-document-pack",
        "name": "Word Document Pack",
        "summary": "Create, edit, and analyze Microsoft Word documents.",
        "description": "Generate DOCX files with headings, tables, images, styles, and formatting. Extract text and metadata from existing documents. Uses python-docx.",
        "capabilities": [{"name": "process_docx", "capability_id": "word_document", "type": "tool"}],
        "entrypoint": "word_document_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "excel-processor-pack",
        "name": "Excel Processor Pack",
        "summary": "Create, read, and manipulate Excel spreadsheets with formulas.",
        "description": "Generate XLSX files with formulas, formatting, charts, and pivot tables. Read and analyze existing spreadsheets. Uses openpyxl.",
        "capabilities": [{"name": "process_xlsx", "capability_id": "excel_processing", "type": "tool"}],
        "entrypoint": "excel_processor_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    # --- Video & Media (Remotion 148K, AI Video 41K) ---
    {
        "slug": "video-generator-pack",
        "name": "Video Generator Pack",
        "summary": "Generate videos from text descriptions, scripts, or templates.",
        "description": "Create videos using AI models and ffmpeg. Support for text-to-video, slideshow creation, subtitle overlay, and basic video editing.",
        "capabilities": [{"name": "generate_video", "capability_id": "video_generation", "type": "tool"}],
        "entrypoint": "video_generator_pack.tool",
        "permissions": {"network": "unrestricted", "filesystem": "temp", "code_execution": "limited_subprocess", "data_access": "input_only", "user_approval": "once"},
    },
    {
        "slug": "audio-processor-pack",
        "name": "Audio Processor Pack",
        "summary": "Edit, convert, and analyze audio files.",
        "description": "Trim, merge, convert audio formats, adjust volume, add effects, and extract metadata. Uses pydub and ffmpeg for audio processing.",
        "capabilities": [{"name": "process_audio", "capability_id": "audio_processing", "type": "tool"}],
        "entrypoint": "audio_processor_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "limited_subprocess", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "gif-creator-pack",
        "name": "GIF Creator Pack",
        "summary": "Create animated GIFs from images, videos, or text.",
        "description": "Generate animated GIFs from image sequences, video clips, or text animations. Optimize file size and control frame rate, dimensions, and quality.",
        "capabilities": [{"name": "create_gif", "capability_id": "gif_creation", "type": "tool"}],
        "entrypoint": "gif_creator_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "limited_subprocess", "data_access": "input_only", "user_approval": "never"},
    },
    # --- Marketing & SEO (SEO 44K, Copywriting 36K) ---
    {
        "slug": "seo-optimizer-pack",
        "name": "SEO Optimizer Pack",
        "summary": "Analyze and optimize web content for search engine rankings.",
        "description": "Audit pages for SEO issues, generate meta tags, analyze keyword density, check accessibility, and provide actionable recommendations for improving search rankings.",
        "capabilities": [{"name": "optimize_seo", "capability_id": "seo_optimization", "type": "tool"}],
        "entrypoint": "seo_optimizer_pack.tool",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "copywriting-pack",
        "name": "Copywriting Pack",
        "summary": "Generate marketing copy, headlines, and ad text with proven formulas.",
        "description": "Create compelling marketing copy using frameworks like AIDA, PAS, and BAB. Generate headlines, product descriptions, email subjects, social posts, and landing page copy.",
        "capabilities": [{"name": "write_copy", "capability_id": "copywriting", "type": "tool"}],
        "entrypoint": "copywriting_pack.tool",
        "permissions": {"network": "none", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "social-media-pack",
        "name": "Social Media Pack",
        "summary": "Schedule, publish, and analyze social media posts across platforms.",
        "description": "Manage social media presence on Twitter/X, LinkedIn, Instagram, and Facebook. Schedule posts, analyze engagement metrics, and generate content calendars.",
        "capabilities": [{"name": "manage_social", "capability_id": "social_media", "type": "tool"}],
        "entrypoint": "social_media_pack.tool",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    # --- Security (Trail of Bits, Secret Scanner) ---
    {
        "slug": "security-audit-pack",
        "name": "Security Audit Pack",
        "summary": "Scan code for security vulnerabilities using static analysis.",
        "description": "Audit code for OWASP Top 10 vulnerabilities, SQL injection, XSS, and more. Uses Semgrep and Bandit rules for Python, JavaScript, and other languages.",
        "capabilities": [{"name": "audit_security", "capability_id": "security_audit", "type": "tool"}],
        "entrypoint": "security_audit_pack.tool",
        "permissions": {"network": "none", "filesystem": "workspace_read", "code_execution": "limited_subprocess", "data_access": "input_only", "user_approval": "once"},
    },
    {
        "slug": "secret-scanner-pack",
        "name": "Secret Scanner Pack",
        "summary": "Detect hardcoded secrets, API keys, and credentials in code.",
        "description": "Scan codebases for accidentally committed secrets, API keys, passwords, tokens, and private keys. Uses pattern matching and entropy analysis.",
        "capabilities": [{"name": "scan_secrets", "capability_id": "secret_scanning", "type": "tool"}],
        "entrypoint": "secret_scanner_pack.tool",
        "permissions": {"network": "none", "filesystem": "workspace_read", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    # --- Code Quality ---
    {
        "slug": "code-linter-pack",
        "name": "Code Linter Pack",
        "summary": "Lint and format code across multiple languages.",
        "description": "Enforce coding standards with support for ESLint, Pylint, Ruff, Prettier, and Black. Auto-fix common issues and generate style reports.",
        "capabilities": [{"name": "lint_code", "capability_id": "code_linting", "type": "tool"}],
        "entrypoint": "code_linter_pack.tool",
        "permissions": {"network": "none", "filesystem": "workspace_read", "code_execution": "limited_subprocess", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "code-refactor-pack",
        "name": "Code Refactor Pack",
        "summary": "Refactor code to improve readability, performance, and maintainability.",
        "description": "Apply DRY principles, extract functions, improve naming, simplify conditionals, and remove dead code. Supports Python, JavaScript, TypeScript, and Go.",
        "capabilities": [{"name": "refactor_code", "capability_id": "code_refactoring", "type": "tool"}],
        "entrypoint": "code_refactor_pack.tool",
        "permissions": {"network": "none", "filesystem": "workspace_write", "code_execution": "none", "data_access": "input_only", "user_approval": "once"},
    },
    {
        "slug": "api-docs-generator-pack",
        "name": "API Docs Generator Pack",
        "summary": "Generate API documentation from code with examples and schemas.",
        "description": "Auto-generate OpenAPI/Swagger documentation, README files, and usage examples from source code. Supports REST, GraphQL, and gRPC APIs.",
        "capabilities": [{"name": "generate_api_docs", "capability_id": "api_documentation", "type": "tool"}],
        "entrypoint": "api_docs_generator_pack.tool",
        "permissions": {"network": "none", "filesystem": "workspace_read", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "test-generator-pack",
        "name": "Test Generator Pack",
        "summary": "Generate unit and integration tests for your codebase.",
        "description": "Auto-generate test cases using pytest, Jest, Vitest, or Go testing. Covers edge cases, error paths, and happy paths with configurable coverage targets.",
        "capabilities": [{"name": "generate_tests", "capability_id": "test_generation", "type": "tool"}],
        "entrypoint": "test_generator_pack.tool",
        "permissions": {"network": "none", "filesystem": "workspace_write", "code_execution": "limited_subprocess", "data_access": "input_only", "user_approval": "once"},
    },
    # --- Design & Assets (web-design 168K, icons) ---
    {
        "slug": "web-design-pack",
        "name": "Web Design Pack",
        "summary": "Generate web design guidelines, color palettes, and typography systems.",
        "description": "Create comprehensive design systems with color palettes, typography scales, spacing systems, and component guidelines. Export as CSS variables or Tailwind config.",
        "capabilities": [{"name": "design_web", "capability_id": "web_design", "type": "tool"}],
        "entrypoint": "web_design_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "icon-generator-pack",
        "name": "Icon Generator Pack",
        "summary": "Generate favicons, app icons, and social media preview images.",
        "description": "Create favicons, Apple touch icons, Android adaptive icons, Open Graph images, and Twitter cards from text, logos, or descriptions. Multiple sizes and formats.",
        "capabilities": [{"name": "generate_icons", "capability_id": "icon_generation", "type": "tool"}],
        "entrypoint": "icon_generator_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    # --- AI & Prompting ---
    {
        "slug": "prompt-engineer-pack",
        "name": "Prompt Engineer Pack",
        "summary": "Design, test, and optimize AI prompts for any model.",
        "description": "Create effective prompts using techniques like chain-of-thought, few-shot, and tree-of-thought. A/B test prompts, measure quality, and optimize for specific models.",
        "capabilities": [{"name": "engineer_prompt", "capability_id": "prompt_engineering", "type": "tool"}],
        "entrypoint": "prompt_engineer_pack.tool",
        "permissions": {"network": "restricted", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    # --- Data Science ---
    {
        "slug": "scientific-computing-pack",
        "name": "Scientific Computing Pack",
        "summary": "Perform scientific calculations with NumPy, pandas, and SciPy.",
        "description": "Run statistical analysis, linear algebra, signal processing, and optimization. Includes support for NumPy arrays, pandas DataFrames, and SciPy functions.",
        "capabilities": [{"name": "compute_science", "capability_id": "scientific_computing", "type": "tool"}],
        "entrypoint": "scientific_computing_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "limited_subprocess", "data_access": "input_only", "user_approval": "never"},
    },
    # --- Legal & Compliance ---
    {
        "slug": "contract-review-pack",
        "name": "Contract Review Pack",
        "summary": "Analyze legal contracts and flag risky clauses.",
        "description": "Review legal contracts, NDAs, and agreements. Identify problematic clauses, missing terms, unusual obligations, and compliance risks. Summarize key terms.",
        "capabilities": [{"name": "review_contract", "capability_id": "contract_review", "type": "tool"}],
        "entrypoint": "contract_review_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "once"},
    },
    {
        "slug": "document-redaction-pack",
        "name": "Document Redaction Pack",
        "summary": "Redact sensitive information like PII, SSNs, and emails from documents.",
        "description": "Automatically detect and redact personally identifiable information, social security numbers, credit card numbers, emails, and phone numbers from documents.",
        "capabilities": [{"name": "redact_document", "capability_id": "document_redaction", "type": "tool"}],
        "entrypoint": "document_redaction_pack.tool",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "citation-manager-pack",
        "name": "Citation Manager Pack",
        "summary": "Format and manage citations in APA, MLA, Chicago, and more.",
        "description": "Generate properly formatted citations from URLs, DOIs, or ISBNs. Supports APA, MLA, Chicago, Harvard, and IEEE citation styles. Create bibliographies.",
        "capabilities": [{"name": "manage_citations", "capability_id": "citation_management", "type": "tool"}],
        "entrypoint": "citation_manager_pack.tool",
        "permissions": {"network": "restricted", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    # --- Cloud ---
    {
        "slug": "azure-toolkit-pack",
        "name": "Azure Toolkit Pack",
        "summary": "Manage Azure cloud resources, VMs, and services.",
        "description": "Interact with Microsoft Azure via the Azure SDK. Manage virtual machines, storage accounts, App Services, Azure Functions, and Cosmos DB.",
        "capabilities": [{"name": "azure_manage", "capability_id": "azure_integration", "type": "tool"}],
        "entrypoint": "azure_toolkit_pack.tool",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "always"},
    },
    # --- Project Planning ---
    {
        "slug": "user-story-planner-pack",
        "name": "User Story Planner Pack",
        "summary": "Break features into user stories with acceptance criteria.",
        "description": "Transform feature requests into structured user stories with descriptions, acceptance criteria, story points, and task breakdowns. Follows Agile best practices.",
        "capabilities": [{"name": "plan_stories", "capability_id": "user_story_planning", "type": "tool"}],
        "entrypoint": "user_story_planner_pack.tool",
        "permissions": {"network": "none", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
]


async def seed():
    async with engine.begin() as conn:
        # Insert taxonomy entries first
        for tax in TAXONOMY_ENTRIES:
            exists = await conn.execute(text("SELECT id FROM capability_taxonomy WHERE id = :id"), {"id": tax["id"]})
            if not exists.scalar():
                await conn.execute(text(
                    "INSERT INTO capability_taxonomy (id, display_name, description, category) "
                    "VALUES (:id, :name, :desc, :cat)"
                ), {"id": tax["id"], "name": tax["display_name"], "desc": tax["description"], "cat": tax["category"]})
                print(f"TAX {tax['id']}")

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

            runtime = pack.get("runtime", "python")

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
                "VALUES (:pkg_id, '1.0.0', 'stable', :manifest, :runtime, "
                "'package', 'agentnode_hosted', :ep, 'cleared') "
                "RETURNING id"
            ), {
                "pkg_id": pkg_id, "manifest": json.dumps(manifest_raw),
                "runtime": runtime, "ep": pack["entrypoint"],
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
                "runtime": runtime,
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
