"""Seed tutorial articles into the blog system.

Usage (on server):
    cd /root/agentnode/backend
    python -m scripts.seed_tutorials

Requires: database connection via app.config.settings
Creates: post type 'tutorial', categories, and all tutorial posts as published.
Skips posts that already exist (by slug).
"""

import asyncio
import json
import math
import os
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Category mapping: slug -> category slug
CATEGORY_MAP = {
    # Cluster 1: Awareness / Getting Started
    "what-is-agentnode-complete-guide-ai-agent-skills": "concepts",
    "what-are-agent-skills-portable-ai-capabilities": "concepts",
    "getting-started-agentnode-install-first-agent-skill": "getting-started",
    "search-discover-agent-skills-agentnode": "getting-started",
    "agentnode-vs-pypi-vs-npm-why-ai-agents-need-own-registry": "concepts",
    # Cluster 2: Building & Publishing
    "build-agent-skill-agentnode-builder": "building",
    "import-langchain-mcp-tools-agentnode": "building",
    "publishing-first-anp-package-complete-guide": "building",
    "anp-manifest-reference": "building",
    "agent-skill-tests-maximize-verification-score": "building",
    # ANP article
    "what-is-anp-open-standard-ai-agent-capabilities": "concepts",
    # Cluster 3: Frameworks & Security
    "using-agentnode-with-langchain-integration-guide": "frameworks",
    "using-agentnode-with-crewai-agent-crews": "frameworks",
    "using-agentnode-mcp-server-claude-cursor": "frameworks",
    "understanding-package-verification-trust-scores": "trust-security",
    "agentnode-security-trust-levels-permissions-safe-installation": "trust-security",
}


def _estimate_reading_time(html: str) -> int:
    text = re.sub(r"<[^>]+>", " ", html)
    words = len(text.split())
    return max(1, math.ceil(words / 200))


def _word_count(html: str) -> int:
    text = re.sub(r"<[^>]+>", " ", html)
    return len(text.split())


async def seed(session: AsyncSession):
    from app.blog.models import BlogCategory, BlogPost, BlogPostType
    from app.auth.models import User

    # 1. Get or create "tutorial" post type
    result = await session.execute(
        select(BlogPostType).where(BlogPostType.slug == "tutorial")
    )
    tutorial_type = result.scalar_one_or_none()
    if not tutorial_type:
        tutorial_type = BlogPostType(
            name="Tutorial",
            slug="tutorial",
            url_prefix="tutorials",
            description="Step-by-step guides for using AgentNode",
            icon="book",
            has_archive=True,
            archive_title="Tutorials",
            archive_description="Learn how to build, publish, and use agent skills with AgentNode.",
            sitemap_priority=0.7,
            sitemap_changefreq="weekly",
            sort_order=1,
        )
        session.add(tutorial_type)
        await session.flush()
        print(f"  Created post type: tutorial (id={tutorial_type.id})")
    else:
        print(f"  Post type 'tutorial' already exists (id={tutorial_type.id})")

    # 2. Create categories
    category_defs = [
        ("getting-started", "Getting Started", "Introduction and onboarding guides"),
        ("building", "Building & Publishing", "How to create and publish agent skills"),
        ("frameworks", "Framework Integration", "Using AgentNode with LangChain, CrewAI, MCP"),
        ("trust-security", "Trust & Security", "Verification, trust levels, and security"),
        ("concepts", "Concepts", "Core concepts and standards behind AgentNode"),
    ]

    categories = {}
    for slug, name, desc in category_defs:
        result = await session.execute(
            select(BlogCategory).where(BlogCategory.slug == slug)
        )
        cat = result.scalar_one_or_none()
        if not cat:
            cat = BlogCategory(name=name, slug=slug, description=desc, sort_order=0)
            session.add(cat)
            await session.flush()
            print(f"  Created category: {slug}")
        categories[slug] = cat

    # 3. Get admin user (first admin, or first user)
    result = await session.execute(
        select(User).where(User.is_admin == True).limit(1)
    )
    author = result.scalar_one_or_none()
    if not author:
        result = await session.execute(select(User).limit(1))
        author = result.scalar_one_or_none()
    if not author:
        print("  ERROR: No users in database. Cannot create posts.")
        return

    print(f"  Author: {author.username} (id={author.id})")

    # 4. Load articles from JSON
    json_path = os.path.join(os.path.dirname(__file__), "tutorial_articles.json")
    with open(json_path, "r", encoding="utf-8") as f:
        articles = json.load(f)

    print(f"  Loaded {len(articles)} articles from JSON")

    # 5. Insert articles
    created = 0
    skipped = 0
    for article in articles:
        # Check if already exists
        result = await session.execute(
            select(BlogPost).where(BlogPost.slug == article["slug"])
        )
        if result.scalar_one_or_none():
            skipped += 1
            continue

        cat_slug = CATEGORY_MAP.get(article["slug"], "getting-started")
        category = categories.get(cat_slug)

        wc = _word_count(article["content_html"])
        rt = _estimate_reading_time(article["content_html"])

        post = BlogPost(
            title=article["title"],
            slug=article["slug"],
            content_html=article["content_html"],
            excerpt=article.get("excerpt", ""),
            seo_title=article.get("seo_title", article["title"][:70]),
            seo_description=article.get("seo_description", "")[:170],
            tags=article.get("tags", []),
            author_id=author.id,
            category_id=category.id if category else None,
            post_type_id=tutorial_type.id,
            status="published",
            published_at=datetime.now(timezone.utc),
            reading_time_min=rt,
            is_featured=article.get("is_featured", False),
        )
        session.add(post)
        created += 1
        print(f"  + {article['slug']} ({wc} words, {rt} min read)")

    await session.commit()
    print(f"\n  Done: {created} created, {skipped} skipped (already exist)")


async def main():
    from app.database import async_engine
    from sqlalchemy.ext.asyncio import AsyncSession as AS
    from sqlalchemy.orm import sessionmaker

    # Import models to register them
    import app.main  # noqa: F401

    async_session = sessionmaker(async_engine, class_=AS, expire_on_commit=False)
    async with async_session() as session:
        print("Seeding tutorial articles...")
        await seed(session)


if __name__ == "__main__":
    asyncio.run(main())
