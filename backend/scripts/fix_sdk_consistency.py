"""Fix all incorrect SDK patterns in article content — both files and production DB.

This script fixes:
1. load_skill → load_tool
2. client.tools.load( → load_tool(
3. client.tools.install( → client.install(
4. client.tools.search( → client.search(
5. Fabricated class imports (Skill, InputSchema, ToolContext, RateLimitConfig, ToolTimeout)
"""
import asyncio
import re
import sys
import glob
import os


# Ordered replacements: most specific first
REPLACEMENTS = [
    # Fix load_skill → load_tool
    ("from agentnode_sdk import load_skill", "from agentnode_sdk import load_tool"),
    ("load_skill(", "load_tool("),

    # Fix client.tools.* patterns
    ("client.tools.load(", "load_tool("),
    ("client.tools.install(", "client.install("),
    ("client.tools.search(", "client.search("),

    # Fix fabricated imports
    ("from agentnode_sdk import Skill, InputSchema, OutputSchema",
     "from agentnode_sdk import AgentNodeClient"),
    ("from agentnode_sdk import ToolContext, ToolResult",
     "from agentnode_sdk import AgentNodeToolError"),
    ("from agentnode_sdk import AgentNode, RateLimitConfig",
     "from agentnode_sdk import AgentNode"),
    ("from agentnode_sdk import AgentNode, ToolError, ToolTimeout",
     "from agentnode_sdk import AgentNode, ToolError"),

    # Fix wrong field names (trust_tier is not a real field — the SDK uses trust_level)
    ("trust_tier", "trust_level"),
    ("min_trust_tier", "min_trust_level"),
]


def fix_content(content: str) -> str:
    """Apply all replacements to content string."""
    for old, new in REPLACEMENTS:
        content = content.replace(old, new)
    return content


def fix_files():
    """Fix all JSON and Python seed files."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    patterns = [
        os.path.join(script_dir, "*.json"),
        os.path.join(script_dir, "*.py"),
    ]

    # Also check seo/ directory
    seo_dir = os.path.join(os.path.dirname(script_dir), "..", "seo")
    if os.path.isdir(seo_dir):
        patterns.append(os.path.join(seo_dir, "*.py"))

    fixed_files = 0
    for pattern in patterns:
        for filepath in glob.glob(pattern):
            if os.path.basename(filepath) == "fix_sdk_consistency.py":
                continue
            if os.path.basename(filepath) == "fix_sdk_imports_in_articles.py":
                continue
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                new_content = fix_content(content)
                if new_content != content:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    print(f"  Fixed file: {os.path.basename(filepath)}")
                    fixed_files += 1
            except Exception as e:
                print(f"  Error on {filepath}: {e}")

    print(f"Fixed {fixed_files} files.")
    return fixed_files


async def fix_database():
    """Fix all articles in the production database."""
    try:
        sys.path.insert(0, "/opt/agentnode/backend")
        from app.database import async_session_factory
        import sqlalchemy
    except ImportError:
        print("Not on production server, skipping DB fix.")
        return 0

    async with async_session_factory() as session:
        result = await session.execute(
            sqlalchemy.text(
                "SELECT id, slug, content_html FROM blog_posts "
                "WHERE content_html IS NOT NULL"
            )
        )
        rows = result.fetchall()
        print(f"Checking {len(rows)} articles in DB...")

        fixed_count = 0
        for row in rows:
            post_id, slug, html = row
            if not html:
                continue
            new_html = fix_content(html)
            if new_html != html:
                await session.execute(
                    sqlalchemy.text(
                        "UPDATE blog_posts SET content_html = :html WHERE id = :id"
                    ),
                    {"html": new_html, "id": post_id},
                )
                fixed_count += 1
                print(f"  Fixed DB article: {slug}")

        await session.commit()
        print(f"Fixed {fixed_count} articles in DB.")
        return fixed_count


if __name__ == "__main__":
    print("=== Fixing files ===")
    fix_files()

    print("\n=== Fixing database ===")
    asyncio.run(fix_database())

    print("\nDone.")
