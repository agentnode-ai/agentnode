"""Seed community MCP packages into the registry.

Creates Package + PackageVersion records directly in the DB for curated
MCP servers. Runs under the agentnode-community system publisher.

Usage:
    python scripts/seed_mcp_packages.py --dry-run
    python scripts/seed_mcp_packages.py
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

# Setup path for app imports
sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.database import async_session_factory  # noqa: E402
from app.auth.models import User  # noqa: E402, F401
from app.packages.models import (  # noqa: E402
    Capability,
    CapabilityTaxonomy,
    CompatibilityRule,
    Package,
    PackageCategory,
    PackageTag,
    PackageVersion,
    Permission,
)
from app.publishers.models import Publisher  # noqa: E402
from app.verification.models import VerificationResult  # noqa: E402, F401

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("seed_mcp")

MANIFEST_VERSION = "0.3"
CATALOG_VERSION = "2026-06-01"
SOURCE = "awesome-mcp-servers"

MCP_CATALOG = [
    {
        "slug": "mcp-brave-search",
        "name": "Brave Search MCP",
        "npm_package": "@modelcontextprotocol/server-brave-search",
        "pinned_version": "2025.3.28",
        "source_repo": "https://github.com/modelcontextprotocol/servers",
        "summary": "MCP server for Brave Search API web and local search.",
        "description": "Provides web and local search capabilities via the Brave Search API. Returns web results with titles, descriptions, and URLs.",
        "env_keys": ["BRAVE_API_KEY"],
        "tools": [{"name": "brave_web_search", "capability_id": "web_search", "description": "Search the web via Brave Search"}],
        "tags": ["mcp", "mcp-server", "search", "brave"],
        "categories": ["search"],
    },
    {
        "slug": "mcp-filesystem",
        "name": "Filesystem MCP",
        "npm_package": "@modelcontextprotocol/server-filesystem",
        "pinned_version": "2025.3.28",
        "source_repo": "https://github.com/modelcontextprotocol/servers",
        "summary": "MCP server for secure local filesystem operations.",
        "description": "Provides read, write, directory listing, and file management capabilities with configurable allowed paths for security.",
        "env_keys": [],
        "tools": [
            {"name": "read_file", "capability_id": "file_read", "description": "Read file contents"},
            {"name": "write_file", "capability_id": "file_write", "description": "Write content to file"},
            {"name": "list_directory", "capability_id": "file_read", "description": "List directory contents"},
        ],
        "tags": ["mcp", "mcp-server", "filesystem", "files"],
        "categories": ["file-management"],
    },
    {
        "slug": "mcp-github",
        "name": "GitHub MCP",
        "npm_package": "@modelcontextprotocol/server-github",
        "pinned_version": "2025.3.28",
        "source_repo": "https://github.com/modelcontextprotocol/servers",
        "summary": "MCP server for GitHub API repository operations.",
        "description": "Interact with GitHub repositories: create issues, pull requests, search code, manage files, and more via the GitHub API.",
        "env_keys": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        "tools": [
            {"name": "create_issue", "capability_id": "project_management", "description": "Create a GitHub issue"},
            {"name": "search_repositories", "capability_id": "web_search", "description": "Search GitHub repositories"},
        ],
        "tags": ["mcp", "mcp-server", "github", "git", "code"],
        "categories": ["developer-tools"],
    },
    {
        "slug": "mcp-memory",
        "name": "Memory MCP",
        "npm_package": "@modelcontextprotocol/server-memory",
        "pinned_version": "2025.3.28",
        "source_repo": "https://github.com/modelcontextprotocol/servers",
        "summary": "MCP server for persistent knowledge graph memory.",
        "description": "Provides a knowledge graph-based memory system for LLMs. Create entities, relations, and query stored knowledge across sessions.",
        "env_keys": [],
        "tools": [
            {"name": "create_entities", "capability_id": "data_storage", "description": "Create entities in the knowledge graph"},
            {"name": "search_nodes", "capability_id": "data_retrieval", "description": "Search the knowledge graph"},
        ],
        "tags": ["mcp", "mcp-server", "memory", "knowledge-graph"],
        "categories": ["data-management"],
    },
    {
        "slug": "mcp-postgres",
        "name": "PostgreSQL MCP",
        "npm_package": "@modelcontextprotocol/server-postgres",
        "pinned_version": "2025.3.28",
        "source_repo": "https://github.com/modelcontextprotocol/servers",
        "summary": "MCP server for read-only PostgreSQL database access.",
        "description": "Query PostgreSQL databases with read-only access. Supports schema inspection and SQL queries.",
        "env_keys": ["POSTGRES_CONNECTION_STRING"],
        "tools": [{"name": "query", "capability_id": "database_query", "description": "Run a read-only SQL query"}],
        "tags": ["mcp", "mcp-server", "postgres", "database", "sql"],
        "categories": ["database"],
    },
    {
        "slug": "mcp-puppeteer",
        "name": "Puppeteer MCP",
        "npm_package": "@modelcontextprotocol/server-puppeteer",
        "pinned_version": "2025.3.28",
        "source_repo": "https://github.com/modelcontextprotocol/servers",
        "summary": "MCP server for browser automation via Puppeteer.",
        "description": "Automate web browsers with Puppeteer. Navigate pages, take screenshots, click elements, fill forms, and extract content.",
        "env_keys": [],
        "tools": [
            {"name": "puppeteer_navigate", "capability_id": "web_automation", "description": "Navigate to a URL"},
            {"name": "puppeteer_screenshot", "capability_id": "web_automation", "description": "Take a screenshot"},
        ],
        "tags": ["mcp", "mcp-server", "puppeteer", "browser", "automation"],
        "categories": ["web-automation"],
    },
    {
        "slug": "mcp-sequential-thinking",
        "name": "Sequential Thinking MCP",
        "npm_package": "@modelcontextprotocol/server-sequential-thinking",
        "pinned_version": "2025.3.28",
        "source_repo": "https://github.com/modelcontextprotocol/servers",
        "summary": "MCP server for structured sequential thinking and reasoning.",
        "description": "Provides a tool for dynamic, reflective problem-solving through sequential thinking steps with revision and branching support.",
        "env_keys": [],
        "tools": [{"name": "sequentialthinking", "capability_id": "reasoning", "description": "Perform sequential thinking steps"}],
        "tags": ["mcp", "mcp-server", "reasoning", "thinking"],
        "categories": ["ai-tools"],
    },
    {
        "slug": "mcp-slack",
        "name": "Slack MCP",
        "npm_package": "@modelcontextprotocol/server-slack",
        "pinned_version": "2025.3.28",
        "source_repo": "https://github.com/modelcontextprotocol/servers",
        "summary": "MCP server for Slack workspace interaction via Bot API.",
        "description": "Interact with Slack workspaces: list channels, post messages, reply to threads, add reactions, and search messages.",
        "env_keys": ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"],
        "tools": [
            {"name": "slack_post_message", "capability_id": "messaging", "description": "Post a message to a Slack channel"},
            {"name": "slack_list_channels", "capability_id": "messaging", "description": "List Slack channels"},
        ],
        "tags": ["mcp", "mcp-server", "slack", "messaging", "chat"],
        "categories": ["communication"],
    },
    {
        "slug": "mcp-sqlite",
        "name": "SQLite MCP",
        "npm_package": "@modelcontextprotocol/server-sqlite",
        "pinned_version": "2025.3.28",
        "source_repo": "https://github.com/modelcontextprotocol/servers",
        "summary": "MCP server for SQLite database interaction and analysis.",
        "description": "Query and manage SQLite databases. Supports read/write queries, schema inspection, and business insight memos.",
        "env_keys": [],
        "tools": [
            {"name": "read_query", "capability_id": "database_query", "description": "Execute a SELECT query"},
            {"name": "write_query", "capability_id": "database_query", "description": "Execute an INSERT/UPDATE/DELETE query"},
        ],
        "tags": ["mcp", "mcp-server", "sqlite", "database", "sql"],
        "categories": ["database"],
    },
    # --- Community-discovered MCPs (pipeline-verified, manually reviewed) ---
    {
        "slug": "mcp-metmuseum",
        "name": "Met Museum MCP",
        "npm_package": "metmuseum-mcp",
        "pinned_version": "1.0.0",
        "source_repo": "https://github.com/mikechao/metmuseum-mcp",
        "summary": "MCP server for The Metropolitan Museum of Art Collection API.",
        "description": "Search and retrieve artwork details from The Met's open collection. Browse departments, search objects by keyword, and get detailed artwork metadata including images.",
        "env_keys": [],
        "tools": [
            {"name": "list-departments", "capability_id": "data_retrieval", "description": "List all departments in the Met Museum"},
            {"name": "search-museum-objects", "capability_id": "web_search", "description": "Search for objects in the Met collection"},
            {"name": "get-museum-object", "capability_id": "data_retrieval", "description": "Get a museum object by its ID"},
            {"name": "open-met-explorer", "capability_id": "web_automation", "description": "Open the interactive Met Explorer app"},
        ],
        "tags": ["mcp", "mcp-server", "museum", "art", "met", "culture"],
        "categories": ["data-management"],
    },
    {
        "slug": "mcp-nakkas",
        "name": "Nakkas SVG MCP",
        "npm_package": "nakkas",
        "pinned_version": "0.1.5",
        "source_repo": "https://github.com/arikusi/nakkas",
        "summary": "MCP server that turns AI into an SVG artist with animated designs.",
        "description": "Generate animated SVG artwork from JSON configuration. AI controls all design decisions including shapes, gradients, filters, and animations.",
        "env_keys": [],
        "tools": [
            {"name": "render_svg", "capability_id": "file_write", "description": "Render animated SVG from JSON config"},
            {"name": "preview", "capability_id": "file_read", "description": "Render SVG content to a PNG image for visual inspection"},
            {"name": "save", "capability_id": "file_write", "description": "Save rendered content to disk as SVG, PNG, or text"},
        ],
        "tags": ["mcp", "mcp-server", "svg", "art", "design", "animation"],
        "categories": ["creative-tools"],
    },
    {
        "slug": "mcp-astronomy-oracle",
        "name": "Astronomy Oracle MCP",
        "npm_package": "astronomy-oracle",
        "pinned_version": "0.1.0",
        "source_repo": "https://github.com/gregario/astronomy-oracle",
        "summary": "MCP server for celestial object catalog and observing session planning.",
        "description": "Accurate astronomical data from 13,000+ deep-sky objects (OpenNGC catalog). Look up celestial objects, search by type or constellation, and plan observing sessions for any location.",
        "env_keys": [],
        "tools": [
            {"name": "lookup_object", "capability_id": "data_retrieval", "description": "Look up a celestial object by Messier/NGC/IC number"},
            {"name": "search_objects", "capability_id": "web_search", "description": "Search and filter the celestial object catalog"},
            {"name": "plan_session", "capability_id": "data_retrieval", "description": "Generate an observing session plan for a location"},
        ],
        "tags": ["mcp", "mcp-server", "astronomy", "science", "space", "stargazing"],
        "categories": ["science"],
    },
    {
        "slug": "mcp-playwright",
        "name": "Playwright MCP",
        "npm_package": "@playwright/mcp",
        "pinned_version": "0.0.75",
        "source_repo": "https://github.com/microsoft/playwright-mcp",
        "summary": "MCP server for browser automation via Playwright. Navigate, click, type, screenshot, and inspect web pages.",
        "description": "Full browser automation for AI agents. Navigate pages, fill forms, click elements, take screenshots, inspect accessibility snapshots, monitor network requests, and execute JavaScript. Chromium auto-downloads on first use. Supports headless mode.",
        "env_keys": [],
        "network_level": "unrestricted",
        "code_execution_level": "shell",
        "tools": [
            {"name": "browser_navigate", "capability_id": "web_automation", "description": "Navigate to a URL"},
            {"name": "browser_click", "capability_id": "web_automation", "description": "Click an element on the page"},
            {"name": "browser_type", "capability_id": "web_automation", "description": "Type text into an editable element"},
            {"name": "browser_fill_form", "capability_id": "web_automation", "description": "Fill multiple form fields"},
            {"name": "browser_take_screenshot", "capability_id": "web_automation", "description": "Take a screenshot of the current page"},
            {"name": "browser_snapshot", "capability_id": "web_automation", "description": "Capture accessibility snapshot of the page"},
            {"name": "browser_evaluate", "capability_id": "web_automation", "description": "Evaluate JavaScript expression on page"},
            {"name": "browser_network_requests", "capability_id": "web_automation", "description": "List network requests since page load"},
        ],
        "tags": ["mcp", "mcp-server", "playwright", "browser", "automation", "testing", "web"],
        "categories": ["web-automation"],
    },
]


def _build_manifest(entry: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "manifest_version": MANIFEST_VERSION,
        "package_id": entry["slug"],
        "name": entry["name"],
        "package_type": "toolpack",
        "runtime": "mcp",
        "install_mode": "package",
        "version": "0.1.0",
        "visibility": "public",
        "hosting_type": "agentnode_hosted",
        "publisher": "agentnode-community",
        "summary": entry["summary"],
        "description": entry["description"],
        "mcp_server": {
            "command": ["npx", "-y", f"{entry['npm_package']}@{entry['pinned_version']}"],
            "transport": "stdio",
            "npm_package": entry["npm_package"],
            "source_repo": entry["source_repo"],
            "env_keys": entry["env_keys"],
        },
        "capabilities": {
            "tools": [
                {
                    **tool,
                    "input_schema": {"type": "object", "properties": {}},
                }
                for tool in entry["tools"]
            ],
            "resources": [],
            "prompts": [],
        },
        "permissions": {
            "network": {"level": entry.get("network_level", "none")},
            "filesystem": {"level": entry.get("filesystem_level", "none")},
            "code_execution": {"level": entry.get("code_execution_level", "none")},
            "data_access": {"level": "input_only"},
            "user_approval": {"required": "once"},
            "external_integrations": [],
        },
        "tags": [t.lower() for t in entry["tags"]],
        "categories": entry["categories"],
        "compatibility": {"frameworks": ["mcp"]},
        "seed_metadata": {
            "seeded_by": "agentnode-community",
            "seeded_at": now,
            "source": SOURCE,
            "npm_version_pinned": entry["pinned_version"],
            "catalog_version": CATALOG_VERSION,
            "claimable": True,
        },
    }


async def seed(dry_run: bool = False, reseed: bool = False) -> None:
    catalog = sorted(MCP_CATALOG, key=lambda x: x["slug"])

    async with async_session_factory() as session:
        # Find system publisher
        result = await session.execute(
            select(Publisher).where(Publisher.slug == "agentnode-community")
        )
        publisher = result.scalar_one_or_none()
        if publisher is None:
            log.error("Publisher 'agentnode-community' not found. Run migration 036 first.")
            sys.exit(1)

        if not publisher.is_system_publisher:
            log.error("Publisher 'agentnode-community' is not a system publisher.")
            sys.exit(1)

        # Reseed: delete existing community MCP packages via raw SQL
        # (ORM cascade is unreliable with circular latest_version_id FK)
        if reseed and not dry_run:
            from sqlalchemy import text
            slugs = [e["slug"] for e in catalog]
            for s in slugs:
                row = (await session.execute(
                    select(Package.id).where(
                        Package.slug == s,
                        Package.publisher_id == publisher.id,
                    )
                )).scalar_one_or_none()
                if row is None:
                    continue
                pkg_id = str(row)
                await session.execute(text(
                    "UPDATE packages SET latest_version_id = NULL WHERE id = :pid"
                ), {"pid": pkg_id})
                await session.execute(text(
                    "DELETE FROM capabilities WHERE package_version_id IN "
                    "(SELECT id FROM package_versions WHERE package_id = :pid)"
                ), {"pid": pkg_id})
                await session.execute(text(
                    "DELETE FROM package_tags WHERE package_version_id IN "
                    "(SELECT id FROM package_versions WHERE package_id = :pid)"
                ), {"pid": pkg_id})
                await session.execute(text(
                    "DELETE FROM package_categories WHERE package_version_id IN "
                    "(SELECT id FROM package_versions WHERE package_id = :pid)"
                ), {"pid": pkg_id})
                await session.execute(text(
                    "DELETE FROM permissions WHERE package_version_id IN "
                    "(SELECT id FROM package_versions WHERE package_id = :pid)"
                ), {"pid": pkg_id})
                await session.execute(text(
                    "DELETE FROM compatibility_rules WHERE package_version_id IN "
                    "(SELECT id FROM package_versions WHERE package_id = :pid)"
                ), {"pid": pkg_id})
                await session.execute(text(
                    "DELETE FROM package_versions WHERE package_id = :pid"
                ), {"pid": pkg_id})
                await session.execute(text(
                    "DELETE FROM packages WHERE id = :pid"
                ), {"pid": pkg_id})
                log.info("DELETED slug=%s (reseed)", s)
            await session.flush()

        created = 0
        skipped = 0

        for entry in catalog:
            slug = entry["slug"]

            if not reseed:
                existing = await session.execute(
                    select(Package).where(Package.slug == slug)
                )
                if existing.scalar_one_or_none() is not None:
                    log.info("SKIPPED slug=%s (exists)", slug)
                    skipped += 1
                    continue

            if dry_run:
                log.info("%s slug=%s (dry-run)", "RESEED" if reseed else "CREATE", slug)
                created += 1
                continue

            manifest = _build_manifest(entry)
            now = datetime.now(timezone.utc)

            pkg = Package(
                id=uuid4(),
                publisher_id=publisher.id,
                slug=slug,
                name=entry["name"],
                package_type="toolpack",
                summary=entry["summary"],
                description=entry["description"],
            )
            session.add(pkg)
            await session.flush()

            pv = PackageVersion(
                id=uuid4(),
                package_id=pkg.id,
                version_number="0.1.0",
                channel="stable",
                manifest_raw=manifest,
                runtime="mcp",
                install_mode="package",
                hosting_type="agentnode_hosted",
                entrypoint=None,
                verification_status="skipped",
                published_at=now,
            )
            session.add(pv)
            await session.flush()

            pkg.latest_version_id = pv.id
            session.add(pkg)

            # Create capabilities (tools) — taxonomy first to satisfy FK
            caps = manifest.get("capabilities", {})
            all_cap_ids = {
                tool.get("capability_id", "general")
                for tool in caps.get("tools", [])
            }
            if all_cap_ids:
                existing_tax = await session.execute(
                    select(CapabilityTaxonomy.id).where(
                        CapabilityTaxonomy.id.in_(all_cap_ids)
                    )
                )
                existing_ids = {row[0] for row in existing_tax.all()}
                for new_id in all_cap_ids - existing_ids:
                    display = new_id.replace("_", " ").title()
                    session.add(CapabilityTaxonomy(
                        id=new_id,
                        display_name=display,
                        description=None,
                        category="uncategorized",
                    ))
                await session.flush()

            for tool in caps.get("tools", []):
                cap_id = tool.get("capability_id", "general")
                session.add(Capability(
                    package_version_id=pv.id,
                    capability_type="tool",
                    capability_id=cap_id,
                    name=tool["name"],
                    description=tool.get("description"),
                    input_schema=tool.get("input_schema"),
                ))

            # Tags
            for tag in manifest.get("tags", []):
                session.add(PackageTag(package_version_id=pv.id, tag=tag))

            # Categories
            for cat in manifest.get("categories", []):
                session.add(PackageCategory(package_version_id=pv.id, category=cat))

            # Compatibility rules
            for fw in manifest.get("compatibility", {}).get("frameworks", []):
                session.add(CompatibilityRule(
                    package_version_id=pv.id,
                    framework=fw,
                ))

            # Permissions
            perms = manifest.get("permissions", {})
            if perms:
                session.add(Permission(
                    package_version_id=pv.id,
                    network_level=perms.get("network", {}).get("level", "none"),
                    allowed_domains=perms.get("network", {}).get("allowed_domains", []),
                    filesystem_level=perms.get("filesystem", {}).get("level", "none"),
                    code_execution_level=perms.get("code_execution", {}).get("level", "none"),
                    data_access_level=perms.get("data_access", {}).get("level", "input_only"),
                    user_approval_level=perms.get("user_approval", {}).get("required", "never"),
                    external_integrations=perms.get("external_integrations", []),
                ))

            log.info("SEEDED slug=%s (%d tools, %d tags)", slug,
                     len(caps.get("tools", [])), len(manifest.get("tags", [])))
            created += 1

        if not dry_run and created > 0:
            await session.commit()
            log.info("DB commit successful: %d created, %d skipped", created, skipped)

            # Search sync (separate from DB transaction)
            try:
                await _sync_search(session, catalog)
            except Exception as exc:
                log.warning("SEARCH_SYNC_FAILED: %s", exc)
        else:
            log.info("Summary: %d would create, %d would skip", created, skipped)


async def _sync_search(session, catalog: list[dict]) -> None:
    from app.packages.service import build_meili_document, sync_package_to_meilisearch

    for entry in sorted(catalog, key=lambda x: x["slug"]):
        result = await session.execute(
            select(Package).where(Package.slug == entry["slug"])
        )
        pkg = result.scalar_one_or_none()
        if pkg is None:
            continue

        pv_result = await session.execute(
            select(PackageVersion).where(PackageVersion.id == pkg.latest_version_id)
        )
        pv = pv_result.scalar_one_or_none()
        if pv is None:
            continue

        try:
            doc = build_meili_document(pkg, pv, pv.manifest_raw)
            await sync_package_to_meilisearch(doc)
            log.info("SEARCH_SYNC_OK slug=%s", entry["slug"])
        except Exception as exc:
            log.warning("SEARCH_SYNC_FAILED slug=%s: %s", entry["slug"], exc)


def main():
    parser = argparse.ArgumentParser(description="Seed community MCP packages")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created without writing")
    parser.add_argument("--reseed", action="store_true", help="Delete existing and re-create with full relations")
    parser.add_argument("--catalog-version", default=CATALOG_VERSION, help="Catalog version tag")
    args = parser.parse_args()

    asyncio.run(seed(dry_run=args.dry_run, reseed=args.reseed))


if __name__ == "__main__":
    main()
