"""Seed Extra SEO blog articles (42 articles) to reach 100 total.

Usage (on server):
    cd /opt/agentnode/backend
    .venv/bin/python -m scripts.seed_blog_extra

Creates: additional blog posts from extra_*.json files.
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
    # Use Cases — Industry (actual slugs from agent output + planned)
    "ai-agent-tools-marketing-automation": "use-cases",
    "ai-agent-tools-sales-teams-crm-automation": "use-cases",
    "ai-agent-tools-marketing-automation-campaigns": "use-cases",
    "ai-agent-tools-sales-prospecting-outreach-automation": "use-cases",
    "ai-agent-tools-hr-recruiting-screening-onboarding": "use-cases",
    "ai-agent-tools-legal-contract-review-compliance": "use-cases",
    "ai-agent-tools-education-personalized-learning": "use-cases",
    "ai-agent-tools-finance-risk-analysis-reporting": "use-cases",
    "ai-agent-tools-healthcare-clinical-documentation": "use-cases",
    "ai-agent-tools-ecommerce-product-management": "use-cases",
    # Use Cases — More (7) — both slug variants covered
    "ai-agent-tools-research-academia-papers": "use-cases",
    "ai-agent-tools-research-literature-review-data": "use-cases",
    "ai-agent-tools-real-estate-property-automation": "use-cases",
    "ai-agent-tools-real-estate-property-analysis": "use-cases",
    "ai-agent-tools-project-management-automation": "use-cases",
    "ai-agent-tools-project-management-planning": "use-cases",
    "ai-agent-tools-seo-content-strategy": "use-cases",
    "ai-agent-tools-seo-content-optimization-ranking": "use-cases",
    "ai-agent-tools-cybersecurity-threat-detection": "use-cases",
    "best-ai-agent-tools-pdf-processing-extraction": "use-cases",
    "ai-agent-tools-pdf-processing-extraction": "use-cases",
    "best-ai-agent-tools-web-scraping-extraction": "use-cases",
    "ai-agent-tools-web-scraping-data-collection": "use-cases",
    # Tool Spotlights — actual slugs from agent output (3 so far + 7 pending)
    "best-ai-agent-tools-database-queries-sql": "use-cases",
    "best-ai-agent-tools-api-integration-automation": "use-cases",
    "best-ai-agent-tools-email-automation": "use-cases",
    # Tool articles — both slug variants (extra_tools.json + extra_tools_b.json)
    "best-image-processing-agent-tools-generate-edit": "use-cases",
    "best-ai-agent-tools-image-processing-analysis": "use-cases",
    "best-text-processing-agent-tools-summarize-translate": "use-cases",
    "best-ai-agent-tools-text-analysis-nlp": "use-cases",
    "best-file-conversion-agent-tools-transform-formats": "use-cases",
    "best-ai-agent-tools-file-conversion-formats": "use-cases",
    "best-search-agent-tools-web-vector-knowledge": "use-cases",
    "best-ai-agent-tools-search-retrieval-rag": "use-cases",
    "best-monitoring-agent-tools-alerts-logs-health": "use-cases",
    "best-ai-agent-tools-monitoring-alerting-observability": "use-cases",
    "best-code-generation-agent-tools-write-test": "use-cases",
    "best-ai-agent-tools-code-generation-development": "use-cases",
    "best-workflow-automation-agent-tools-orchestrate": "use-cases",
    "best-ai-agent-tools-workflow-automation-orchestration": "use-cases",
    # Frameworks (7)
    "agentnode-google-adk-agent-development-kit": "frameworks",
    "agentnode-dify-visual-ai-workflow-integration": "frameworks",
    "agentnode-n8n-no-code-ai-agent-automation": "frameworks",
    "agentnode-flowise-drag-drop-agent-builder": "frameworks",
    "agentnode-openai-assistants-api-tool-integration": "frameworks",
    "building-ai-agents-claude-tool-use-agentnode": "frameworks",
    "building-custom-ai-agent-framework-architecture": "frameworks",
    # Security (5) — actual slugs from agent output
    "enterprise-ai-agent-security-ciso-guide": "security-research",
    "soc2-compliance-ai-agent-tools-audit": "security-research",
    "ai-agent-permission-models-least-privilege": "security-research",
    "sandboxing-ai-agent-tools-isolation-strategies": "security-research",
    "audit-trails-ai-agents-logging-monitoring": "security-research",
    # Ecosystem (5) — actual slugs from agent output
    "ai-agent-predictions-2027-future-trends": "ecosystem",
    "ai-tool-economy-developer-earnings-agent-era": "ecosystem",
    "agent-orchestration-patterns-architectures-scale": "ecosystem",
    "future-ai-registries-lessons-from-npm": "ecosystem",
    "why-every-ai-team-needs-tool-strategy": "ecosystem",
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

    # 4. Load articles from all extra JSON files
    all_articles = []
    json_dir = os.path.dirname(__file__)
    for filename in sorted(os.listdir(json_dir)):
        if filename.startswith("extra_") and filename.endswith(".json"):
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
        print("Seeding Extra SEO blog articles...")
        await seed(session)


if __name__ == "__main__":
    asyncio.run(main())
