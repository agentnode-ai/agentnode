"""Seed Waves 2-4 SEO blog articles into the blog system.

Usage (on server):
    cd /opt/agentnode/backend
    .venv/bin/python -m scripts.seed_blog_wave234

Creates: additional blog categories if missing + 28 Wave 2-4 SEO articles as published blog posts.
Uses the existing "post" post type (url_prefix="blog").
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


# Category mapping: article slug -> category slug
CATEGORY_MAP = {
    # Wave 2
    "agentnode-openai-function-calling-integration": "frameworks",
    "langchain-vs-crewai-vs-autogen-comparison": "comparisons",
    "best-mcp-server-registry-verified-tools": "comparisons",
    "build-ai-agent-finds-own-tools-autonomously": "use-cases",
    "agent-tool-verification-why-it-matters": "security-research",
    "agent-skills-every-ai-developer-should-know": "use-cases",
    "what-is-anp-open-standard-portable-agent-tools": "concepts",
    "ai-agent-tools-cursor-mcp-setup-guide": "frameworks",
    "ai-agent-tools-marketplace-next-npm-for-agents": "ecosystem",
    "trusted-mcp-servers-verify-before-install": "use-cases",
    # Wave 3
    "build-multi-agent-system-shared-tools": "use-cases",
    "smithery-vs-agentnode-mcp-server-registry-comparison": "comparisons",
    "ai-agent-tools-customer-support-automation": "use-cases",
    "import-langchain-tools-agentnode-migration": "building",
    "ai-agent-supply-chain-security-lessons": "security-research",
    "skills-sh-vs-agentnode-agent-skill-directory": "comparisons",
    "add-ai-capabilities-python-application-agentnode": "getting-started",
    "ai-agent-tools-data-analysis-extract-transform": "use-cases",
    "building-agent-skills-tests-maximize-verification-score": "building",
    "mcp-vs-anp-ai-agent-tool-standards-compared": "concepts",
    # Wave 4
    "build-code-review-agent-ai-tools": "use-cases",
    "state-of-ai-agent-frameworks-2026": "ecosystem",
    "ai-agent-tools-devops-automate-infrastructure": "use-cases",
    "pypi-package-to-agent-skill-conversion-guide": "building",
    "how-agents-choose-tools-resolution-engine": "concepts",
    "agent-skills-content-creators-writing-image-video": "use-cases",
    "open-source-ai-agent-tools-build-share-verify": "ecosystem",
    "agentnode-autogen-semantic-kernel-integration": "frameworks",
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

    # 1. Get existing "post" post type
    result = await session.execute(
        select(BlogPostType).where(BlogPostType.slug == "post")
    )
    post_type = result.scalar_one_or_none()
    if not post_type:
        print("  ERROR: No 'post' post type found.")
        return

    print(f"  Post type: {post_type.name} (id={post_type.id})")

    # 2. Ensure all needed categories exist
    category_defs = [
        ("building", "Building & Publishing", "How to create and publish agent skills", 5),
        ("getting-started", "Getting Started", "Introduction and onboarding guides", 3),
        ("concepts", "Concepts", "Core concepts and standards behind AgentNode", 7),
        ("frameworks", "Framework Integration", "Using AgentNode with LangChain, CrewAI, MCP", 8),
        ("security-research", "Security Research", "Industry vulnerability analyses, attack breakdowns, and security advisories", 10),
        ("comparisons", "Comparisons & Alternatives", "Head-to-head registry and framework comparisons", 20),
        ("monetization", "Monetization & Business", "How to earn revenue with AI agent tools and skills", 30),
        ("use-cases", "Use Cases & Solutions", "Practical applications and curated tool recommendations", 40),
        ("ecosystem", "AI Agent Ecosystem", "Industry trends, framework overviews, and thought leadership", 50),
    ]

    categories = {}
    existing_cats = await session.execute(select(BlogCategory))
    for cat in existing_cats.scalars():
        categories[cat.slug] = cat

    for slug, name, desc, sort in category_defs:
        if slug in categories:
            continue
        cat = BlogCategory(name=name, slug=slug, description=desc, sort_order=sort)
        session.add(cat)
        await session.flush()
        categories[slug] = cat
        print(f"  Created category: {slug}")

    # 3. Get admin author
    result = await session.execute(
        select(User).where(User.is_admin == True).limit(1)
    )
    author = result.scalar_one_or_none()
    if not author:
        result = await session.execute(select(User).limit(1))
        author = result.scalar_one_or_none()
    if not author:
        print("  ERROR: No users in database.")
        return

    print(f"  Author: {author.username} (id={author.id})")

    # 4. Load articles from all wave JSON files
    all_articles = []
    json_dir = os.path.dirname(__file__)
    for filename in sorted(os.listdir(json_dir)):
        if filename.startswith(("wave2_", "wave3_", "wave4_", "wave34_")) and filename.endswith(".json"):
            path = os.path.join(json_dir, filename)
            with open(path, "r", encoding="utf-8") as f:
                articles = json.load(f)
                all_articles.extend(articles)
                print(f"  Loaded {len(articles)} articles from {filename}")

    print(f"  Total: {len(all_articles)} articles to seed")

    # 5. Insert articles
    created = 0
    skipped = 0
    for article in all_articles:
        result = await session.execute(
            select(BlogPost).where(BlogPost.slug == article["slug"])
        )
        if result.scalar_one_or_none():
            skipped += 1
            continue

        cat_slug = CATEGORY_MAP.get(article["slug"])
        category = categories.get(cat_slug) if cat_slug else None

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
            post_type_id=post_type.id,
            status="published",
            published_at=datetime.now(timezone.utc),
            reading_time_min=rt,
            is_featured=article.get("is_featured", False),
        )
        session.add(post)
        created += 1
        print(f"  + {article['slug']} ({wc} words, {rt} min, cat={cat_slug})")

    await session.commit()
    print(f"\n  Done: {created} created, {skipped} skipped")


async def main():
    import app.main  # noqa: F401
    from app.database import async_session_factory

    async with async_session_factory() as session:
        print("Seeding Waves 2-4 SEO blog articles...")
        await seed(session)


if __name__ == "__main__":
    asyncio.run(main())
