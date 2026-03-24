"""Seed Wave 1 SEO blog articles into the blog system.

Usage (on server):
    cd /root/agentnode/backend
    python -m scripts.seed_blog_wave1

Creates: 5 new blog categories + 12 Wave 1 SEO articles as published blog posts.
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
    # Security Research
    "clawhavoc-malicious-ai-agent-skills-attack-explained": "security-research",
    "mcp-server-security-path-traversal-vulnerabilities": "security-research",
    "ai-agent-security-threats-vulnerabilities-2026": "security-research",
    # Use Cases
    "best-mcp-servers-claude-2026-verified": "use-cases",
    "best-ai-agent-tools-developers-2026": "use-cases",
    # Building & Publishing
    "how-to-build-mcp-server-tutorial": "building",
    "how-to-publish-mcp-server-registry": "building",
    # Monetization
    "how-to-sell-ai-agent-tools-monetize-skills": "monetization",
    "how-much-earn-selling-ai-agent-tools-revenue": "monetization",
    "top-selling-agent-skills-successful-ai-tools": "monetization",
    # Comparisons
    "clawhub-alternatives-safer-ai-agent-tool-registries": "comparisons",
    "composio-alternatives-open-agent-tool-platforms": "comparisons",
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

    # 1. Get existing "post" post type (url_prefix="blog")
    result = await session.execute(
        select(BlogPostType).where(BlogPostType.slug == "post")
    )
    post_type = result.scalar_one_or_none()
    if not post_type:
        print("  ERROR: No 'post' post type found. Run migrations first.")
        return

    print(f"  Post type: {post_type.name} (id={post_type.id}, prefix={post_type.url_prefix})")

    # 2. Create new SEO categories (skip if exist)
    category_defs = [
        # Ensure categories from tutorials seed exist (idempotent — skipped if already present)
        ("building", "Building & Publishing", "How to create and publish agent skills", 5),
        # New SEO categories
        ("security-research", "Security Research", "Industry vulnerability analyses, attack breakdowns, and security advisories", 10),
        ("comparisons", "Comparisons & Alternatives", "Head-to-head registry and framework comparisons", 20),
        ("monetization", "Monetization & Business", "How to earn revenue with AI agent tools and skills", 30),
        ("use-cases", "Use Cases & Solutions", "Practical applications and curated tool recommendations", 40),
        ("ecosystem", "AI Agent Ecosystem", "Industry trends, framework overviews, and thought leadership", 50),
    ]

    categories = {}
    # Also load existing categories so we can map articles to them
    existing_cats = await session.execute(select(BlogCategory))
    for cat in existing_cats.scalars():
        categories[cat.slug] = cat

    for slug, name, desc, sort in category_defs:
        if slug in categories:
            print(f"  Category '{slug}' already exists")
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

    # 4. Load articles from JSON
    json_path = os.path.join(os.path.dirname(__file__), "wave1_articles.json")
    with open(json_path, "r", encoding="utf-8") as f:
        articles = json.load(f)

    print(f"  Loaded {len(articles)} articles from wave1_articles.json")

    # 5. Insert articles
    created = 0
    skipped = 0
    for article in articles:
        result = await session.execute(
            select(BlogPost).where(BlogPost.slug == article["slug"])
        )
        if result.scalar_one_or_none():
            skipped += 1
            print(f"  SKIP (exists): {article['slug']}")
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
        print(f"  + {article['slug']} ({wc} words, {rt} min read, cat={cat_slug})")

    await session.commit()
    print(f"\n  Done: {created} created, {skipped} skipped (already exist)")


async def main():
    import app.main  # noqa: F401
    from app.database import async_session_factory

    async with async_session_factory() as session:
        print("Seeding Wave 1 SEO blog articles...")
        await seed(session)


if __name__ == "__main__":
    asyncio.run(main())
