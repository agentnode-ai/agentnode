#!/usr/bin/env python3
"""Generate 30 first-party agent starter-packs for AgentNode (V2).

Each agent follows the AgentContext contract v1:
    def run(context: AgentContext, **kwargs) -> dict

Tools are called via context.run_tool(slug, tool_name, **kwargs).
Real package slugs, real data flow, real synthesis.
"""

import os
import textwrap

# ───── Helper code included in every agent.py ─────
_HELPER = '''\
def _call(ctx, slug, tool_name=None, **kw):
    """Call a tool via AgentContext. Returns (success: bool, data: dict)."""
    r = ctx.run_tool(slug, tool_name, **kw)
    if r.success:
        return True, (r.result if isinstance(r.result, dict) else {"output": r.result})
    return False, {"error": r.error or "unknown"}
'''

# ───── Agent definitions ─────
# Each agent has metadata + run_code (the body of run(), at 4-space indent)

AGENTS = [
    # ══════════════════════════════════════════════════════════════
    # Research & Analysis (6)
    # ══════════════════════════════════════════════════════════════
    {
        "slug": "deep-research-agent",
        "name": "Deep Research Agent",
        "summary": "Conduct deep multi-source research on any topic, synthesize findings into a structured report.",
        "goal": "Research a topic using web search, page extraction, and summarization to produce a report with sources.",
        "tags": ["agent", "research", "analysis"],
        "category": "research",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read"},
        "run_code": """\
    topic = kwargs.get("topic", "") or context.goal

    # Step 1: Search the web
    context.next_iteration()
    ok, search = _call(context, "web-search-pack", "search_web",
                       query=topic, max_results=10)
    if not ok:
        return {"error": f"Search failed: {search.get('error')}", "done": False}

    hits = search.get("results", [])
    sources = []
    texts = []

    # Step 2: Extract content from top results
    for item in hits[:5]:
        url = item.get("url", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", "extract_webpage", url=url)
        if ok and page.get("text"):
            texts.append(page["text"][:3000])
            sources.append({"title": item.get("title", url), "url": url})

    if not texts:
        return {"report": "No content could be extracted from search results.",
                "search_results": [{"title": h.get("title", ""), "url": h.get("url", "")} for h in hits],
                "done": True}

    # Step 3: Summarize combined content
    context.next_iteration()
    combined = "\\n\\n---\\n\\n".join(texts)
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=combined, max_sentences=10)

    report = summary.get("summary", combined[:1000]) if ok else combined[:1000]

    return {"report": report, "sources": sources, "topic": topic,
            "pages_analyzed": len(texts), "done": True}
""",
    },
    {
        "slug": "academic-research-agent",
        "name": "Academic Research Agent",
        "summary": "Search academic papers on arXiv, extract content, and produce a literature review.",
        "goal": "Conduct academic research by searching paper databases, extracting PDFs, and summarizing into a review.",
        "tags": ["agent", "research", "academic", "papers"],
        "category": "research",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read"},
        "run_code": """\
    topic = kwargs.get("topic", "") or context.goal

    # Step 1: Search for academic papers
    context.next_iteration()
    ok, search = _call(context, "web-search-pack", "search_web",
                       query=f"site:arxiv.org OR site:scholar.google.com {topic}",
                       max_results=10)
    if not ok:
        return {"error": f"Search failed: {search.get('error')}", "done": False}

    hits = search.get("results", [])
    papers = []
    texts = []

    # Step 2: Extract content — try PDF for arxiv, webpage for others
    for item in hits[:5]:
        url = item.get("url", "")
        if not url:
            continue
        context.next_iteration()

        if "arxiv.org" in url and "/abs/" in url:
            pdf_url = url.replace("/abs/", "/pdf/") + ".pdf"
            ok, pdf = _call(context, "pdf-extractor-pack", "pdf_extraction",
                            file_path=pdf_url, pages="1-5")
            if ok and pdf.get("text"):
                texts.append(pdf["text"][:3000])
                papers.append({"title": item.get("title", ""), "url": url, "type": "pdf"})
                continue

        ok, page = _call(context, "webpage-extractor-pack", "extract_webpage", url=url)
        if ok and page.get("text"):
            texts.append(page["text"][:3000])
            papers.append({"title": item.get("title", url), "url": url, "type": "webpage"})

    if not texts:
        return {"review": "No academic content found.", "papers": [], "done": True}

    # Step 3: Summarize into a literature review
    context.next_iteration()
    combined = "\\n\\n---\\n\\n".join(texts)
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=combined, max_sentences=12)

    review = summary.get("summary", combined[:1000]) if ok else combined[:1000]

    return {"review": review, "papers": papers, "topic": topic,
            "sources_found": len(papers), "done": True}
""",
    },
    {
        "slug": "competitive-intel-agent",
        "name": "Competitive Intelligence Agent",
        "summary": "Analyze competitors by scraping web presence, monitoring news, and producing a competitive report.",
        "goal": "Gather competitive intelligence by searching the web, extracting content, and summarizing into an analysis.",
        "tags": ["agent", "research", "competitive", "business"],
        "category": "research",
        "permissions": {"network": "unrestricted", "filesystem": "none"},
        "run_code": """\
    company = kwargs.get("company", "") or context.goal

    # Step 1: Search for company information
    context.next_iteration()
    ok, search = _call(context, "web-search-pack", "search_web",
                       query=f"{company} company overview products services competitors",
                       max_results=8)
    if not ok:
        return {"error": f"Search failed: {search.get('error')}", "done": False}

    hits = search.get("results", [])
    sources = []
    texts = []

    # Step 2: Extract content from top results
    for item in hits[:4]:
        url = item.get("url", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", "extract_webpage", url=url)
        if ok and page.get("text"):
            texts.append(page["text"][:2000])
            sources.append({"title": item.get("title", url), "url": url})

    # Step 3: Search for recent news
    context.next_iteration()
    ok, news_search = _call(context, "web-search-pack", "search_web",
                            query=f"{company} news latest developments 2026",
                            max_results=5)
    news_items = news_search.get("results", []) if ok else []

    # Step 4: Summarize findings
    context.next_iteration()
    combined = "\\n\\n".join(texts) if texts else f"Company: {company}"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=combined, max_sentences=8)
    analysis = summary.get("summary", combined[:800]) if ok else combined[:800]

    return {"analysis": analysis, "company": company, "sources": sources,
            "recent_news": [{"title": n.get("title", ""), "url": n.get("url", "")}
                            for n in news_items],
            "done": True}
""",
    },
    {
        "slug": "seo-research-agent",
        "name": "SEO Research Agent",
        "summary": "Audit a website's SEO by analyzing content, keywords, and competitor rankings.",
        "goal": "Perform SEO analysis by extracting page content, analyzing SEO factors, and checking competitor rankings.",
        "tags": ["agent", "seo", "marketing", "analysis"],
        "category": "research",
        "permissions": {"network": "unrestricted", "filesystem": "none"},
        "run_code": """\
    target_url = kwargs.get("url", "") or context.goal
    keyword = kwargs.get("keyword", "")

    # Step 1: Extract target page content
    context.next_iteration()
    ok, page = _call(context, "webpage-extractor-pack", "extract_webpage",
                     url=target_url, include_links=True)
    page_text = page.get("text", "") if ok else ""
    page_title = page.get("title", "") if ok else ""

    # Step 2: Run SEO analysis on the page
    context.next_iteration()
    ok, seo = _call(context, "seo-optimizer-pack", "webpage_extraction",
                    html=page_text, url=target_url, keyword=keyword)
    seo_findings = seo if ok else {}

    # Step 3: Check competitor rankings for the keyword
    context.next_iteration()
    search_query = keyword if keyword else page_title
    ok, competitors = _call(context, "web-search-pack", "search_web",
                            query=search_query, max_results=10)
    competitor_urls = []
    if ok:
        for r in competitors.get("results", []):
            competitor_urls.append({"title": r.get("title", ""), "url": r.get("url", ""),
                                    "snippet": r.get("snippet", "")})

    # Step 4: Summarize findings
    context.next_iteration()
    findings_text = f"Page: {target_url}\\nTitle: {page_title}\\n"
    if seo_findings:
        findings_text += f"SEO Analysis: {seo_findings}\\n"
    findings_text += f"Content length: {len(page_text)} chars"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=findings_text, max_sentences=6)

    return {"url": target_url, "page_title": page_title,
            "seo_analysis": seo_findings, "competitor_rankings": competitor_urls,
            "summary": summary.get("summary", "") if ok else findings_text[:500],
            "content_length": len(page_text), "done": True}
""",
    },
    {
        "slug": "fact-check-agent",
        "name": "Fact Check Agent",
        "summary": "Verify claims against multiple web sources and produce a fact-check verdict with evidence.",
        "goal": "Verify a claim by searching for supporting and contradicting evidence, then producing a verdict.",
        "tags": ["agent", "fact-check", "verification", "research"],
        "category": "research",
        "permissions": {"network": "unrestricted", "filesystem": "none"},
        "run_code": """\
    claim = kwargs.get("claim", "") or context.goal

    # Step 1: Search for supporting evidence
    context.next_iteration()
    ok, support_search = _call(context, "web-search-pack", "search_web",
                               query=f"evidence supporting \\"{claim}\\"",
                               max_results=5)
    supporting = support_search.get("results", []) if ok else []

    # Step 2: Search for contradicting evidence
    context.next_iteration()
    ok, contra_search = _call(context, "web-search-pack", "search_web",
                              query=f"evidence against debunk \\"{claim}\\"",
                              max_results=5)
    contradicting = contra_search.get("results", []) if ok else []

    # Step 3: Extract content from top sources
    all_sources = []
    support_texts = []
    contra_texts = []

    for item in supporting[:3]:
        url = item.get("url", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", "extract_webpage", url=url)
        if ok and page.get("text"):
            support_texts.append(page["text"][:1500])
            all_sources.append({"title": item.get("title", ""), "url": url, "stance": "supporting"})

    for item in contradicting[:3]:
        url = item.get("url", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", "extract_webpage", url=url)
        if ok and page.get("text"):
            contra_texts.append(page["text"][:1500])
            all_sources.append({"title": item.get("title", ""), "url": url, "stance": "contradicting"})

    # Step 4: Synthesize verdict
    support_count = len(support_texts)
    contra_count = len(contra_texts)
    total = support_count + contra_count

    if total == 0:
        verdict = "unverifiable"
        confidence = 0.0
    elif contra_count == 0:
        verdict = "likely_true"
        confidence = min(0.9, 0.5 + support_count * 0.15)
    elif support_count == 0:
        verdict = "likely_false"
        confidence = min(0.9, 0.5 + contra_count * 0.15)
    else:
        ratio = support_count / total
        if ratio > 0.7:
            verdict = "likely_true"
        elif ratio < 0.3:
            verdict = "likely_false"
        else:
            verdict = "disputed"
        confidence = round(abs(ratio - 0.5) * 2, 2)

    return {"claim": claim, "verdict": verdict, "confidence": confidence,
            "supporting_sources": support_count, "contradicting_sources": contra_count,
            "sources": all_sources, "done": True}
""",
    },
    {
        "slug": "news-digest-agent",
        "name": "News Digest Agent",
        "summary": "Aggregate news from multiple sources on a topic, summarize stories, and optionally translate.",
        "goal": "Create a news digest by aggregating articles, summarizing each, and optionally translating.",
        "tags": ["agent", "news", "digest", "multilingual"],
        "category": "research",
        "permissions": {"network": "unrestricted", "filesystem": "none"},
        "run_code": """\
    topic = kwargs.get("topic", "") or context.goal
    target_language = kwargs.get("target_language", "")

    # Step 1: Aggregate news
    context.next_iteration()
    ok, news = _call(context, "news-aggregator-pack", "web_search",
                     topic=topic, limit=10)
    articles_raw = news.get("results", news.get("articles", [])) if ok else []

    # Fallback to web search if aggregator returns nothing
    if not articles_raw:
        context.next_iteration()
        ok, search = _call(context, "web-search-pack", "search_web",
                           query=f"{topic} news latest", max_results=10)
        articles_raw = search.get("results", []) if ok else []

    # Step 2: Extract and summarize each article
    digest_items = []
    for item in articles_raw[:6]:
        url = item.get("url", item.get("link", ""))
        title = item.get("title", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", "extract_webpage", url=url)
        text = page.get("text", "") if ok else ""

        summary_text = ""
        if text:
            ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                                text=text[:3000], max_sentences=3)
            summary_text = summary.get("summary", text[:200]) if ok else text[:200]

        digest_items.append({"title": title, "url": url, "summary": summary_text})

    # Step 3: Optional translation
    if target_language and digest_items:
        context.next_iteration()
        full_digest = "\\n\\n".join(
            f"## {d['title']}\\n{d['summary']}" for d in digest_items
        )
        ok, translated = _call(context, "text-translator-pack", "translation",
                               text=full_digest, target_language=target_language)
        if ok:
            return {"digest": translated.get("translated_text", translated.get("output", full_digest)),
                    "articles": digest_items, "topic": topic,
                    "language": target_language, "done": True}

    return {"digest": digest_items, "topic": topic,
            "article_count": len(digest_items), "done": True}
""",
    },

    # ══════════════════════════════════════════════════════════════
    # Content Creation (5)
    # ══════════════════════════════════════════════════════════════
    {
        "slug": "blog-writer-agent",
        "name": "Blog Writer Agent",
        "summary": "Write SEO-optimized blog posts with compelling structure, using LLM reasoning.",
        "goal": "Write SEO-optimized blog posts",
        "tags": ["agent", "content", "blog", "seo", "writing"],
        "category": "content",
        "tier": "llm_only",
        "permissions": {"network": "none", "filesystem": "none"},
        "system_prompt": """\
You are an expert blog writer and content strategist.
Create engaging, well-structured blog posts with:
- Compelling headline
- Hook introduction that draws readers in
- Clear H2/H3 structure for scanability
- Actionable takeaways backed by reasoning
- Conclusion with call-to-action
- SEO considerations: natural keyword usage, meta description suggestion

Write in a professional but approachable tone. Use concrete examples.
Format output as markdown.""",
        "run_code": """\
    topic = kwargs.get("topic", "") or context.goal
    audience = kwargs.get("audience", "general readers")
    tone = kwargs.get("tone", "professional but approachable")

    prompt = (
        f"Write a comprehensive blog post about: {topic}\\n\\n"
        f"Target audience: {audience}\\n"
        f"Tone: {tone}\\n\\n"
        "Include:\\n"
        "1. A compelling headline\\n"
        "2. An engaging introduction\\n"
        "3. 3-5 main sections with H2 headers\\n"
        "4. Actionable takeaways\\n"
        "5. A conclusion with call-to-action\\n"
        "6. A suggested meta description (1 sentence)\\n\\n"
        "Format as markdown."
    )

    article = context.call_llm_text([
        {"role": "user", "content": prompt}
    ])

    return {"article": article, "title": topic, "done": True}
""",
    },
    {
        "slug": "technical-docs-agent",
        "name": "Technical Documentation Agent",
        "summary": "Generate API documentation and developer guides from source code.",
        "goal": "Generate documentation by analyzing code structure, extracting signatures, and formatting guides.",
        "tags": ["agent", "docs", "api", "code", "developer"],
        "category": "content",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "run_code": """\
    code = kwargs.get("code", "") or context.goal

    # Step 1: Analyze code structure
    context.next_iteration()
    ok, analysis = _call(context, "code-refactor-pack", "code_analysis",
                         code=code, operation="analyze")
    code_structure = analysis if ok else {}

    # Step 2: Generate test stubs to understand function signatures
    context.next_iteration()
    ok, tests = _call(context, "test-generator-pack", "code_analysis",
                      code=code, framework="pytest")
    test_stubs = tests if ok else {}

    # Step 3: Lint code to find documentation gaps
    context.next_iteration()
    ok, lint = _call(context, "code-linter-pack", "code_analysis",
                     code=code, language="python")
    lint_issues = lint if ok else {}

    # Assemble documentation
    doc = "# API Documentation\\n\\n"

    if code_structure:
        doc += "## Code Structure\\n\\n"
        for key, val in code_structure.items():
            if key != "error":
                doc += f"- **{key}**: {val}\\n"
        doc += "\\n"

    if test_stubs:
        doc += "## Function Signatures\\n\\n"
        test_code = test_stubs.get("tests", test_stubs.get("output", ""))
        if isinstance(test_code, str):
            doc += f"```python\\n{test_code[:2000]}\\n```\\n\\n"

    if lint_issues:
        doc += "## Quality Notes\\n\\n"
        issues = lint_issues.get("issues", lint_issues.get("output", ""))
        if isinstance(issues, (list, str)):
            doc += f"{issues}\\n"

    return {"documentation": doc, "code_structure": code_structure,
            "test_stubs": test_stubs, "done": True}
""",
    },
    {
        "slug": "newsletter-agent",
        "name": "Newsletter Agent",
        "summary": "Draft engaging newsletter emails on any topic, using LLM reasoning.",
        "goal": "Create engaging newsletter content",
        "tags": ["agent", "newsletter", "email", "content", "curation"],
        "category": "content",
        "tier": "llm_only",
        "permissions": {"network": "none", "filesystem": "none"},
        "system_prompt": """\
You are a newsletter editor and content curator.
Create engaging, scannable newsletter emails that:
- Open with a compelling hook or key insight
- Present 3-5 story sections with clear headlines
- Keep each section concise (2-3 sentences)
- Use a conversational, informed tone
- End with a call-to-action or teaser for next issue
- Include suggested subject line

Write content based on your knowledge. Be specific and informative.
Format the newsletter as a professional email.""",
        "run_code": """\
    topic = kwargs.get("topic", "") or context.goal
    sender_name = kwargs.get("sender_name", "Newsletter Bot")
    audience = kwargs.get("audience", "subscribers")

    prompt = (
        f"Write a newsletter email about: {topic}\\n\\n"
        f"Sender name: {sender_name}\\n"
        f"Target audience: {audience}\\n\\n"
        "Include:\\n"
        "1. A catchy subject line\\n"
        "2. An opening hook\\n"
        "3. 3-5 story sections with headlines and brief descriptions\\n"
        "4. A closing with call-to-action\\n\\n"
        "Write based on your knowledge of the topic. Be specific and current."
    )

    newsletter = context.call_llm_text([
        {"role": "user", "content": prompt}
    ])

    return {"newsletter": newsletter, "topic": topic, "done": True}
""",
    },
    {
        "slug": "social-media-agent",
        "name": "Social Media Agent",
        "summary": "Create platform-optimized social media posts with copy and hashtags, using LLM reasoning.",
        "goal": "Generate platform-specific social media content",
        "tags": ["agent", "social-media", "content", "marketing"],
        "category": "content",
        "tier": "llm_only",
        "permissions": {"network": "none", "filesystem": "none"},
        "system_prompt": """\
You are a social media content strategist and copywriter.
Create platform-optimized posts that:
- Match each platform's tone and constraints
- Twitter/X: witty, concise, max 280 chars, relevant hashtags
- LinkedIn: professional, insightful, thought-leadership angle
- Instagram: visual-friendly caption, casual tone, hashtag groups
- Include engagement hooks (questions, CTAs)
- Focus on value and shareability

Respond with a JSON object containing posts for each platform.""",
        "run_code": """\
    import json as _json

    topic = kwargs.get("topic", "") or context.goal
    platforms = kwargs.get("platforms", "twitter,linkedin,instagram")

    prompt = (
        f"Create social media posts about: {topic}\\n\\n"
        f"Platforms: {platforms}\\n\\n"
        "For each platform, write an optimized post.\\n"
        "Respond with a JSON object like:\\n"
        '{\\n'
        '  "key_message": "one-sentence summary",\\n'
        '  "posts": {\\n'
        '    "twitter": "tweet text with #hashtags",\\n'
        '    "linkedin": "professional post...",\\n'
        '    "instagram": "caption with #hashtags"\\n'
        '  }\\n'
        '}\\n'
        "Only output the JSON, no other text."
    )

    raw = context.call_llm_text([
        {"role": "user", "content": prompt}
    ])

    # Parse JSON response, fall back to raw text
    try:
        data = _json.loads(raw)
        posts = data.get("posts", {})
        key_message = data.get("key_message", topic)
    except (_json.JSONDecodeError, TypeError):
        posts = {"raw": raw}
        key_message = topic

    return {"posts": posts, "key_message": key_message,
            "topic": topic, "done": True}
""",
    },
    {
        "slug": "report-generator-agent",
        "name": "Report Generator Agent",
        "summary": "Generate structured business reports with executive summary from provided data, using LLM reasoning.",
        "goal": "Generate structured business reports",
        "tags": ["agent", "report", "data", "analytics", "business"],
        "category": "content",
        "tier": "llm_only",
        "permissions": {"network": "none", "filesystem": "none"},
        "system_prompt": """\
You are a business analyst and report writer.
Generate clear, structured reports that include:
- Executive summary (2-3 key takeaways)
- Data analysis and insights
- Key metrics and trends
- Recommendations
- Conclusion

Use professional business language. Support claims with reasoning.
Structure reports with clear headers and bullet points.
Format as markdown.""",
        "run_code": """\
    data = kwargs.get("data", "") or kwargs.get("text", "") or context.goal
    report_type = kwargs.get("report_type", "business analysis")
    audience = kwargs.get("audience", "stakeholders")

    prompt = (
        f"Generate a {report_type} report based on the following information:\\n\\n"
        f"{data}\\n\\n"
        f"Target audience: {audience}\\n\\n"
        "Include:\\n"
        "1. Executive summary with key takeaways\\n"
        "2. Detailed analysis\\n"
        "3. Key findings and insights\\n"
        "4. Recommendations\\n"
        "5. Conclusion\\n\\n"
        "Format as a professional markdown report."
    )

    report = context.call_llm_text([
        {"role": "user", "content": prompt}
    ])

    return {"report": report, "report_type": report_type, "done": True}
""",
    },

    # ══════════════════════════════════════════════════════════════
    # Data & Analytics (5)
    # ══════════════════════════════════════════════════════════════
    {
        "slug": "csv-analyst-agent",
        "name": "CSV Analyst Agent",
        "summary": "Upload a CSV, detect patterns and anomalies, and produce an analysis report.",
        "goal": "Analyze a CSV by detecting data types, finding patterns, and producing an analysis report.",
        "tags": ["agent", "csv", "data", "analytics"],
        "category": "data",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "run_code": """\
    file_path = kwargs.get("file_path", "") or context.goal

    # Step 1: Describe the dataset
    context.next_iteration()
    ok, desc = _call(context, "csv-analyzer-pack", "describe_csv", file_path=file_path)
    statistics = desc if ok else {"error": "Could not describe dataset"}

    # Step 2: Inspect columns
    context.next_iteration()
    ok, cols = _call(context, "csv-analyzer-pack", "columns_csv", file_path=file_path)
    columns = cols if ok else {}

    # Step 3: Sample first rows
    context.next_iteration()
    ok, head = _call(context, "csv-analyzer-pack", "head_csv", file_path=file_path, n=10)
    sample = head if ok else {}

    # Step 4: Summarize findings
    context.next_iteration()
    findings = f"File: {file_path}\\nStats: {statistics}\\nColumns: {columns}"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=findings, max_sentences=6)

    return {"analysis": summary.get("summary", findings[:500]) if ok else findings[:500],
            "statistics": statistics, "columns": columns,
            "sample_data": sample, "file": file_path, "done": True}
""",
    },
    {
        "slug": "log-investigator-agent",
        "name": "Log Investigator Agent",
        "summary": "Parse log files, identify errors and anomalies, correlate events, and produce a report.",
        "goal": "Investigate logs by parsing entries, identifying error patterns, and generating an incident report.",
        "tags": ["agent", "logs", "debugging", "incident", "devops"],
        "category": "data",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read"},
        "run_code": """\
    file_path = kwargs.get("file_path", "")
    log_text = kwargs.get("log_text", "") or context.goal

    # Step 1: If file_path given, analyze as CSV (structured logs)
    context.next_iteration()
    log_entries = []
    if file_path:
        ok, desc = _call(context, "csv-analyzer-pack", "describe_csv", file_path=file_path)
        if ok:
            log_entries.append(f"Log statistics: {desc}")
        ok, head = _call(context, "csv-analyzer-pack", "head_csv", file_path=file_path, n=20)
        if ok:
            log_entries.append(f"Recent entries: {head}")

    # Step 2: Process log text as JSON if structured
    context.next_iteration()
    if log_text and (log_text.strip().startswith("[") or log_text.strip().startswith("{")):
        import json
        try:
            data = json.loads(log_text) if isinstance(log_text, str) else log_text
            if isinstance(data, dict):
                data = [data]
            if isinstance(data, list):
                ok, processed = _call(context, "json-processor-pack", "json_processing",
                                      data=data, query="[?level=='ERROR' || level=='error']")
                if ok:
                    log_entries.append(f"Error entries: {processed}")
        except (json.JSONDecodeError, TypeError):
            log_entries.append(log_text[:3000])
    elif log_text:
        log_entries.append(log_text[:3000])

    # Step 3: Search for common error patterns
    context.next_iteration()
    combined = "\\n".join(log_entries)
    # Extract error-like lines
    error_lines = [line for line in combined.split("\\n")
                   if any(kw in line.lower() for kw in ["error", "exception", "fail", "critical"])]

    if error_lines:
        sample_error = error_lines[0][:100]
        ok, web = _call(context, "web-search-pack", "search_web",
                        query=f"how to fix {sample_error}", max_results=3)
        remediation = [r.get("title", "") for r in web.get("results", [])] if ok else []
    else:
        remediation = []

    # Step 4: Summarize
    context.next_iteration()
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=combined[:4000], max_sentences=6)

    return {"findings": summary.get("summary", combined[:500]) if ok else combined[:500],
            "error_count": len(error_lines),
            "sample_errors": error_lines[:5],
            "remediation_hints": remediation,
            "done": True}
""",
    },
    {
        "slug": "data-pipeline-agent",
        "name": "Data Pipeline Agent",
        "summary": "Build and run a data pipeline: load from CSV/JSON, clean and transform, then output.",
        "goal": "Execute a data pipeline by loading, cleaning, and transforming data to a target format.",
        "tags": ["agent", "etl", "data", "pipeline", "transformation"],
        "category": "data",
        "permissions": {"network": "restricted", "filesystem": "workspace_write"},
        "run_code": """\
    file_path = kwargs.get("file_path", "") or context.goal
    filter_column = kwargs.get("filter_column", "")
    filter_value = kwargs.get("filter_value", "")
    filter_operator = kwargs.get("filter_operator", "==")

    # Step 1: Describe the source data
    context.next_iteration()
    ok, desc = _call(context, "csv-analyzer-pack", "describe_csv", file_path=file_path)
    source_stats = desc if ok else {}

    # Step 2: Inspect columns
    context.next_iteration()
    ok, cols = _call(context, "csv-analyzer-pack", "columns_csv", file_path=file_path)
    column_info = cols if ok else {}

    # Step 3: Apply filter if specified
    context.next_iteration()
    filtered_data = None
    if filter_column and filter_value:
        ok, filtered = _call(context, "csv-analyzer-pack", "filter_csv",
                             file_path=file_path, column=filter_column,
                             value=filter_value, operator=filter_operator)
        if ok:
            filtered_data = filtered

    # Step 4: Get processed data sample
    context.next_iteration()
    ok, head = _call(context, "csv-analyzer-pack", "head_csv", file_path=file_path, n=20)
    sample = head if ok else {}

    records_note = "all records"
    if filtered_data:
        records_note = f"filtered by {filter_column} {filter_operator} {filter_value}"

    return {"source_file": file_path, "source_stats": source_stats,
            "columns": column_info, "filter_applied": records_note,
            "filtered_data": filtered_data, "sample": sample,
            "done": True}
""",
    },
    {
        "slug": "spreadsheet-auditor-agent",
        "name": "Spreadsheet Auditor Agent",
        "summary": "Audit CSV/Excel spreadsheets for errors, duplicates, and data inconsistencies.",
        "goal": "Audit a spreadsheet by checking for duplicates, type mismatches, and missing values.",
        "tags": ["agent", "spreadsheet", "audit", "excel", "quality"],
        "category": "data",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "run_code": """\
    file_path = kwargs.get("file_path", "") or context.goal

    # Step 1: Describe to get overall statistics
    context.next_iteration()
    ok, desc = _call(context, "csv-analyzer-pack", "describe_csv", file_path=file_path)
    statistics = desc if ok else {}

    # Step 2: Column-level analysis
    context.next_iteration()
    ok, cols = _call(context, "csv-analyzer-pack", "columns_csv", file_path=file_path)
    columns = cols if ok else {}

    # Step 3: Inspect sample data for issues
    context.next_iteration()
    ok, head = _call(context, "csv-analyzer-pack", "head_csv", file_path=file_path, n=20)
    sample = head if ok else {}

    # Step 4: Identify potential issues from statistics
    issues = []
    if isinstance(statistics, dict):
        for col_name, stats in statistics.items():
            if isinstance(stats, dict):
                if stats.get("count", 0) == 0:
                    issues.append(f"Empty column: {col_name}")
                null_count = stats.get("null_count", stats.get("missing", 0))
                if null_count and null_count > 0:
                    issues.append(f"Missing values in {col_name}: {null_count}")

    # Step 5: Summarize audit
    context.next_iteration()
    audit_text = f"File: {file_path}\\nIssues found: {len(issues)}\\n"
    audit_text += "\\n".join(issues[:20]) if issues else "No obvious issues detected."
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=audit_text, max_sentences=5)

    quality_score = max(0, 100 - len(issues) * 10)

    return {"audit_summary": summary.get("summary", audit_text) if ok else audit_text,
            "issues": issues, "quality_score": quality_score,
            "statistics": statistics, "columns": columns,
            "file": file_path, "done": True}
""",
    },
    {
        "slug": "sql-report-agent",
        "name": "SQL Report Agent",
        "summary": "Answer natural language questions about data by generating SQL queries.",
        "goal": "Translate natural language to SQL, format queries, and produce a report.",
        "tags": ["agent", "sql", "database", "report", "analytics"],
        "category": "data",
        "permissions": {"network": "restricted", "filesystem": "none"},
        "run_code": """\
    question = kwargs.get("question", "") or context.goal
    schema = kwargs.get("schema", "")
    dialect = kwargs.get("dialect", "postgresql")

    # Step 1: Generate SQL from natural language
    context.next_iteration()
    ok, gen = _call(context, "sql-generator-pack", "generate_sql",
                    description=question, schema=schema, dialect=dialect)
    if not ok:
        return {"error": f"SQL generation failed: {gen.get('error')}", "done": False}

    raw_sql = gen.get("sql", gen.get("output", ""))

    # Step 2: Format the SQL
    context.next_iteration()
    formatted_sql = raw_sql
    if raw_sql:
        ok, fmt = _call(context, "sql-generator-pack", "format_sql",
                        sql=raw_sql, dialect=dialect)
        if ok:
            formatted_sql = fmt.get("formatted_sql", fmt.get("output", raw_sql))

    # Step 3: Summarize what the query does
    context.next_iteration()
    explanation_text = f"Question: {question}\\nGenerated SQL: {raw_sql}"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=explanation_text, max_sentences=3)

    return {"question": question, "sql": formatted_sql,
            "explanation": summary.get("summary", "") if ok else "",
            "dialect": dialect, "done": True}
""",
    },

    # ══════════════════════════════════════════════════════════════
    # Development (5)
    # ══════════════════════════════════════════════════════════════
    {
        "slug": "code-review-agent",
        "name": "Code Review Agent",
        "summary": "Perform comprehensive code review: lint, security audit, and refactoring suggestions.",
        "goal": "Review code by running linting, security checks, and suggesting refactorings.",
        "tags": ["agent", "code-review", "security", "quality", "developer"],
        "category": "development",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "run_code": """\
    code = kwargs.get("code", "") or context.goal

    # Step 1: Lint the code
    context.next_iteration()
    ok, lint = _call(context, "code-linter-pack", "code_analysis",
                     code=code, language="python")
    lint_result = lint if ok else {"error": "Linting failed"}

    # Step 2: Security audit
    context.next_iteration()
    ok, security = _call(context, "security-audit-pack", "code_analysis",
                         code=code, severity="LOW")
    security_result = security if ok else {"error": "Security audit failed"}

    # Step 3: Refactoring analysis
    context.next_iteration()
    ok, refactor = _call(context, "code-refactor-pack", "code_analysis",
                         code=code, operation="analyze")
    refactor_result = refactor if ok else {"error": "Refactoring analysis failed"}

    # Step 4: Scan for secrets
    context.next_iteration()
    ok, secrets = _call(context, "secret-scanner-pack", "code_analysis", code=code)
    secrets_result = secrets if ok else {}

    # Compile review
    review_sections = []
    if lint_result and "error" not in lint_result:
        review_sections.append(f"## Linting\\n{lint_result}")
    if security_result and "error" not in security_result:
        review_sections.append(f"## Security\\n{security_result}")
    if refactor_result and "error" not in refactor_result:
        review_sections.append(f"## Refactoring\\n{refactor_result}")
    if secrets_result:
        review_sections.append(f"## Secrets Scan\\n{secrets_result}")

    return {"review": "\\n\\n".join(review_sections),
            "lint": lint_result, "security": security_result,
            "refactoring": refactor_result, "secrets": secrets_result,
            "done": True}
""",
    },
    {
        "slug": "test-writer-agent",
        "name": "Test Writer Agent",
        "summary": "Analyze source code and generate comprehensive test suites with unit tests.",
        "goal": "Generate tests by analyzing function signatures, creating unit tests, and validating them.",
        "tags": ["agent", "testing", "code", "quality", "developer"],
        "category": "development",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "run_code": """\
    code = kwargs.get("code", "") or context.goal
    framework = kwargs.get("framework", "pytest")

    # Step 1: Analyze code structure
    context.next_iteration()
    ok, analysis = _call(context, "code-refactor-pack", "code_analysis",
                         code=code, operation="analyze")
    code_structure = analysis if ok else {}

    # Step 2: Generate tests
    context.next_iteration()
    ok, tests = _call(context, "test-generator-pack", "code_analysis",
                      code=code, framework=framework)
    generated_tests = tests if ok else {"error": "Test generation failed"}

    # Step 3: Lint the generated tests
    test_code = ""
    if ok:
        test_code = tests.get("tests", tests.get("output", tests.get("code", "")))
        if isinstance(test_code, str) and test_code:
            context.next_iteration()
            ok, lint = _call(context, "code-linter-pack", "code_analysis",
                             code=test_code, language="python")
            if ok and lint.get("issues"):
                generated_tests["lint_issues"] = lint["issues"]

    return {"tests": test_code, "code_structure": code_structure,
            "framework": framework, "generated": generated_tests,
            "done": True}
""",
    },
    {
        "slug": "dependency-audit-agent",
        "name": "Dependency Audit Agent",
        "summary": "Scan project dependencies for vulnerabilities, outdated versions, and leaked secrets.",
        "goal": "Audit dependencies by scanning for CVEs, checking versions, and detecting leaked secrets.",
        "tags": ["agent", "security", "dependencies", "audit", "devops"],
        "category": "development",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read"},
        "run_code": """\
    code = kwargs.get("code", "") or context.goal  # requirements.txt or pyproject.toml content

    # Step 1: Analyze the dependency file
    context.next_iteration()
    ok, analysis = _call(context, "code-refactor-pack", "code_analysis",
                         code=code, operation="analyze")
    deps_info = analysis if ok else {}

    # Step 2: Search for known vulnerabilities
    context.next_iteration()
    # Extract package names from the code (rough heuristic)
    lines = code.strip().split("\\n")
    packages = []
    for line in lines[:20]:
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("["):
            pkg = line.split(">=")[0].split("==")[0].split("<")[0].split(">")[0].strip()
            if pkg:
                packages.append(pkg)

    vulnerabilities = []
    for pkg in packages[:5]:
        context.next_iteration()
        ok, search = _call(context, "web-search-pack", "search_web",
                           query=f"{pkg} python CVE vulnerability 2025 2026",
                           max_results=3)
        if ok:
            for r in search.get("results", []):
                if any(kw in r.get("title", "").lower() for kw in ["cve", "vuln", "security"]):
                    vulnerabilities.append({
                        "package": pkg,
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                    })

    # Step 3: Scan for leaked secrets
    context.next_iteration()
    ok, secrets = _call(context, "secret-scanner-pack", "code_analysis", code=code)
    secrets_found = secrets if ok else {}

    return {"packages_scanned": packages, "vulnerabilities": vulnerabilities,
            "secrets_scan": secrets_found, "dependency_info": deps_info,
            "done": True}
""",
    },
    {
        "slug": "ci-cd-agent",
        "name": "CI/CD Agent",
        "summary": "Analyze project structure and generate CI/CD pipeline configuration.",
        "goal": "Orchestrate a CI/CD pipeline by analyzing code, running checks, and generating config.",
        "tags": ["agent", "cicd", "devops", "deployment", "docker"],
        "category": "development",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read", "code_execution": "limited_subprocess"},
        "run_code": """\
    code = kwargs.get("code", "") or context.goal

    # Step 1: Analyze project structure
    context.next_iteration()
    ok, analysis = _call(context, "code-refactor-pack", "code_analysis",
                         code=code, operation="analyze")
    project_info = analysis if ok else {}

    # Step 2: Lint the code
    context.next_iteration()
    ok, lint = _call(context, "code-linter-pack", "code_analysis",
                     code=code, language="python", fix=False)
    lint_result = lint if ok else {}

    # Step 3: Generate test stubs
    context.next_iteration()
    ok, tests = _call(context, "test-generator-pack", "code_analysis",
                      code=code, framework="pytest")
    test_info = tests if ok else {}

    # Step 4: Generate pipeline recommendation
    context.next_iteration()
    pipeline_text = f"Project analysis: {project_info}\\nLint status: {lint_result}"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=pipeline_text, max_sentences=5)

    has_issues = bool(lint_result.get("issues", []))
    has_tests = bool(test_info.get("tests", test_info.get("output", "")))

    pipeline_steps = ["checkout", "install_dependencies"]
    if has_tests:
        pipeline_steps.append("run_tests")
    pipeline_steps.append("lint")
    if not has_issues:
        pipeline_steps.extend(["build", "deploy"])

    return {"pipeline_steps": pipeline_steps,
            "code_analysis": project_info,
            "lint_status": lint_result,
            "test_status": test_info,
            "recommendation": summary.get("summary", "") if ok else "",
            "ready_to_deploy": not has_issues,
            "done": True}
""",
    },
    {
        "slug": "api-design-agent",
        "name": "API Design Agent",
        "summary": "Generate an OpenAPI specification from requirements, validate it, and produce docs.",
        "goal": "Design an API by converting requirements into a spec, validating, and generating docs.",
        "tags": ["agent", "api", "openapi", "design", "developer"],
        "category": "development",
        "permissions": {"network": "none", "filesystem": "workspace_write"},
        "run_code": """\
    requirements = kwargs.get("requirements", "") or context.goal
    dialect = kwargs.get("dialect", "postgresql")

    # Step 1: Generate SQL for data models
    context.next_iteration()
    ok, sql = _call(context, "sql-generator-pack", "generate_sql",
                    description=f"Create tables for: {requirements}",
                    dialect=dialect)
    data_model_sql = sql.get("sql", sql.get("output", "")) if ok else ""

    # Step 2: Format the SQL
    context.next_iteration()
    formatted_sql = data_model_sql
    if data_model_sql:
        ok, fmt = _call(context, "sql-generator-pack", "format_sql",
                        sql=data_model_sql, dialect=dialect)
        if ok:
            formatted_sql = fmt.get("formatted_sql", fmt.get("output", data_model_sql))

    # Step 3: Analyze for best practices
    context.next_iteration()
    if data_model_sql:
        ok, lint = _call(context, "code-linter-pack", "code_analysis",
                         code=data_model_sql, language="python")
    else:
        lint = {}

    # Step 4: Summarize the design
    context.next_iteration()
    design_text = f"Requirements: {requirements}\\nData Model SQL: {formatted_sql}"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=design_text, max_sentences=5)

    return {"requirements": requirements,
            "data_model_sql": formatted_sql,
            "design_summary": summary.get("summary", "") if ok else "",
            "done": True}
""",
    },

    # ══════════════════════════════════════════════════════════════
    # Business & Productivity (5)
    # ══════════════════════════════════════════════════════════════
    {
        "slug": "email-triage-agent",
        "name": "Email Triage Agent",
        "summary": "Prioritize incoming emails, draft responses for routine messages, extract action items.",
        "goal": "Triage emails by categorizing priority, drafting responses, and extracting action items.",
        "tags": ["agent", "email", "productivity", "triage"],
        "category": "productivity",
        "permissions": {"network": "restricted", "filesystem": "none"},
        "run_code": """\
    emails_text = kwargs.get("emails", "") or context.goal

    # Step 1: Summarize the emails
    context.next_iteration()
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=emails_text[:5000], max_sentences=10)
    email_summary = summary.get("summary", emails_text[:500]) if ok else emails_text[:500]

    # Step 2: Draft response for the email
    context.next_iteration()
    ok, response = _call(context, "email-drafter-pack", "email_drafting",
                         intent=f"Reply to: {email_summary[:500]}",
                         tone="professional")
    draft = response.get("email", response.get("output", "")) if ok else ""

    # Step 3: Extract action items by summarizing again with focus
    context.next_iteration()
    ok, actions = _call(context, "document-summarizer-pack", "document_summary",
                        text=f"Extract action items from: {emails_text[:3000]}",
                        max_sentences=5)

    # Simple priority classification based on keywords
    text_lower = emails_text.lower()
    if any(w in text_lower for w in ["urgent", "asap", "critical", "deadline"]):
        priority = "high"
    elif any(w in text_lower for w in ["important", "please review", "action required"]):
        priority = "medium"
    else:
        priority = "low"

    return {"summary": email_summary, "priority": priority,
            "draft_response": draft,
            "action_items": actions.get("summary", "") if ok else "",
            "done": True}
""",
    },
    {
        "slug": "meeting-prep-agent",
        "name": "Meeting Prep Agent",
        "summary": "Prepare for meetings by researching attendees, summarizing docs, and generating an agenda.",
        "goal": "Prepare for a meeting by researching topics and attendees, then generating an agenda.",
        "tags": ["agent", "meeting", "productivity", "preparation"],
        "category": "productivity",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read"},
        "run_code": """\
    topic = kwargs.get("topic", "") or context.goal
    attendees = kwargs.get("attendees", "")

    # Step 1: Research the meeting topic
    context.next_iteration()
    ok, topic_search = _call(context, "web-search-pack", "search_web",
                             query=topic, max_results=5)
    topic_results = topic_search.get("results", []) if ok else []

    # Step 2: Research attendees if provided
    attendee_info = []
    if attendees:
        for person in attendees.split(",")[:3]:
            person = person.strip()
            if not person:
                continue
            context.next_iteration()
            ok, search = _call(context, "web-search-pack", "search_web",
                               query=f"{person} professional background", max_results=3)
            if ok:
                snippets = [r.get("snippet", "") for r in search.get("results", [])]
                attendee_info.append({"name": person, "background": " ".join(snippets)[:300]})

    # Step 3: Extract key content from topic results
    context.next_iteration()
    topic_texts = []
    for item in topic_results[:3]:
        url = item.get("url", "")
        if url:
            ok, page = _call(context, "webpage-extractor-pack", "extract_webpage", url=url)
            if ok and page.get("text"):
                topic_texts.append(page["text"][:1500])

    # Step 4: Summarize into agenda
    context.next_iteration()
    prep_text = f"Meeting topic: {topic}\\n"
    if topic_texts:
        prep_text += "Background: " + "\\n".join(topic_texts)[:2000]
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=prep_text, max_sentences=8)

    agenda = f"# Meeting: {topic}\\n\\n"
    agenda += "## Key Points\\n"
    agenda += (summary.get("summary", "") if ok else prep_text[:500]) + "\\n\\n"
    if attendee_info:
        agenda += "## Attendees\\n"
        for a in attendee_info:
            agenda += f"- **{a['name']}**: {a['background']}\\n"

    return {"agenda": agenda, "topic": topic,
            "attendee_research": attendee_info,
            "background_sources": [r.get("url", "") for r in topic_results],
            "done": True}
""",
    },
    {
        "slug": "project-planner-agent",
        "name": "Project Planner Agent",
        "summary": "Break down project goals into user stories, tasks, and milestones, using LLM reasoning.",
        "goal": "Plan projects with structured breakdown",
        "tags": ["agent", "project", "planning", "agile", "productivity"],
        "category": "productivity",
        "tier": "llm_only",
        "permissions": {"network": "none", "filesystem": "none"},
        "system_prompt": """\
You are a senior project manager and agile coach.
Create detailed, actionable project plans that include:
- Clear scope definition
- User stories in "As a [role], I want [feature], so that [benefit]" format
- Task breakdown with effort estimates (S/M/L)
- Milestones with dependencies
- Risk assessment
- Definition of Done for each milestone

Use agile methodology. Be specific and practical.
Format as markdown.""",
        "run_code": """\
    project = kwargs.get("project", "") or context.goal
    methodology = kwargs.get("methodology", "agile")
    team_size = kwargs.get("team_size", "")

    prompt = (
        f"Create a project plan for: {project}\\n\\n"
        f"Methodology: {methodology}\\n"
    )
    if team_size:
        prompt += f"Team size: {team_size}\\n"
    prompt += (
        "\\nInclude:\\n"
        "1. Scope definition\\n"
        "2. User stories\\n"
        "3. Task breakdown with effort estimates\\n"
        "4. Milestones and timeline\\n"
        "5. Risk assessment\\n"
        "6. Definition of Done\\n\\n"
        "Format as a structured markdown document."
    )

    plan = context.call_llm_text([
        {"role": "user", "content": prompt}
    ])

    return {"plan": plan, "project": project, "done": True}
""",
    },
    {
        "slug": "contract-review-agent",
        "name": "Contract Review Agent",
        "summary": "Analyze legal contracts, flag risky clauses, compare against templates.",
        "goal": "Review a contract by extracting text, identifying risks, and suggesting amendments.",
        "tags": ["agent", "legal", "contract", "review", "compliance"],
        "category": "productivity",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "run_code": """\
    text = kwargs.get("text", "")
    file_path = kwargs.get("file_path", "")

    # Step 1: Extract text from PDF if file provided
    context.next_iteration()
    contract_text = text
    if file_path and not text:
        ok, pdf = _call(context, "pdf-extractor-pack", "pdf_extraction",
                        file_path=file_path, extract_tables=True)
        if ok:
            contract_text = pdf.get("text", "")

    if not contract_text:
        contract_text = context.goal

    # Step 2: Analyze contract for risks
    context.next_iteration()
    ok, review = _call(context, "contract-review-pack", "document_parsing",
                       text=contract_text[:5000], check_risks=True, extract_terms=True)
    contract_analysis = review if ok else {}

    # Step 3: Summarize the contract
    context.next_iteration()
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=contract_text[:5000], max_sentences=8)
    contract_summary = summary.get("summary", contract_text[:500]) if ok else contract_text[:500]

    return {"summary": contract_summary,
            "risk_analysis": contract_analysis.get("risks", contract_analysis),
            "key_terms": contract_analysis.get("terms", {}),
            "text_length": len(contract_text),
            "done": True}
""",
    },
    {
        "slug": "crm-enrichment-agent",
        "name": "CRM Enrichment Agent",
        "summary": "Enrich CRM contacts with web data: company info, social profiles, and news.",
        "goal": "Enrich contact records by searching for company info, profiles, and news.",
        "tags": ["agent", "crm", "sales", "enrichment", "business"],
        "category": "productivity",
        "permissions": {"network": "unrestricted", "filesystem": "none"},
        "run_code": """\
    contact = kwargs.get("contact", "") or context.goal
    company = kwargs.get("company", "")

    # Step 1: Search for the contact/person
    context.next_iteration()
    ok, person_search = _call(context, "web-search-pack", "search_web",
                              query=f"{contact} professional profile linkedin",
                              max_results=5)
    person_results = person_search.get("results", []) if ok else []

    # Step 2: Search for company info
    context.next_iteration()
    company_info = {}
    if company:
        ok, company_search = _call(context, "web-search-pack", "search_web",
                                   query=f"{company} company about products",
                                   max_results=5)
        if ok:
            company_info = {"results": company_search.get("results", [])}

    # Step 3: Extract details from top results
    profile_texts = []
    for item in person_results[:3]:
        url = item.get("url", "")
        if not url:
            continue
        context.next_iteration()
        ok, page = _call(context, "webpage-extractor-pack", "extract_webpage", url=url)
        if ok and page.get("text"):
            profile_texts.append(page["text"][:1500])

    # Step 4: Search for recent news
    context.next_iteration()
    search_name = f"{contact} {company}" if company else contact
    ok, news = _call(context, "web-search-pack", "search_web",
                     query=f"{search_name} news recent", max_results=5)
    news_items = news.get("results", []) if ok else []

    # Step 5: Summarize profile
    context.next_iteration()
    combined = "\\n".join(profile_texts) if profile_texts else contact
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=combined, max_sentences=5)

    return {"contact": contact, "company": company,
            "profile_summary": summary.get("summary", "") if ok else "",
            "social_links": [r.get("url", "") for r in person_results[:3]],
            "company_info": company_info,
            "recent_news": [{"title": n.get("title", ""), "url": n.get("url", "")}
                            for n in news_items],
            "done": True}
""",
    },

    # ══════════════════════════════════════════════════════════════
    # Monitoring & Ops (4)
    # ══════════════════════════════════════════════════════════════
    {
        "slug": "website-monitor-agent",
        "name": "Website Monitor Agent",
        "summary": "Monitor websites for content changes, downtime, and extract current state.",
        "goal": "Monitor target websites by checking content, comparing snapshots, and reporting status.",
        "tags": ["agent", "monitoring", "website", "alerts", "devops"],
        "category": "ops",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_write"},
        "run_code": """\
    url = kwargs.get("url", "") or context.goal
    previous_snapshot = kwargs.get("previous_text", "")

    # Step 1: Extract current page content
    context.next_iteration()
    ok, page = _call(context, "webpage-extractor-pack", "extract_webpage",
                     url=url, include_links=True)
    if not ok:
        return {"url": url, "status": "down",
                "error": page.get("error", "Could not reach site"), "done": True}

    current_text = page.get("text", "")
    current_title = page.get("title", "")

    # Step 2: Check if content changed vs previous snapshot
    changes_detected = False
    if previous_snapshot:
        changes_detected = current_text.strip() != previous_snapshot.strip()

    # Step 3: Summarize current content
    context.next_iteration()
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=current_text[:3000], max_sentences=5)
    content_summary = summary.get("summary", current_text[:300]) if ok else current_text[:300]

    # Step 4: Check for common issues via search
    context.next_iteration()
    ok, uptime = _call(context, "web-search-pack", "search_web",
                       query=f"is {url} down today", max_results=3)
    uptime_reports = [r.get("title", "") for r in uptime.get("results", [])] if ok else []

    return {"url": url, "status": "up", "title": current_title,
            "content_summary": content_summary,
            "content_length": len(current_text),
            "changes_detected": changes_detected,
            "uptime_reports": uptime_reports,
            "snapshot": current_text[:5000],
            "done": True}
""",
    },
    {
        "slug": "security-scanner-agent",
        "name": "Security Scanner Agent",
        "summary": "Run comprehensive security scan: SAST, dependency vulnerabilities, secret detection.",
        "goal": "Perform security scan by running static analysis, checking for CVEs, and detecting secrets.",
        "tags": ["agent", "security", "scanning", "compliance", "devops"],
        "category": "ops",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read"},
        "run_code": """\
    code = kwargs.get("code", "") or context.goal

    # Step 1: Static analysis (lint)
    context.next_iteration()
    ok, lint = _call(context, "code-linter-pack", "code_analysis",
                     code=code, language="python")
    lint_findings = lint if ok else {}

    # Step 2: Security-specific audit (bandit)
    context.next_iteration()
    ok, security = _call(context, "security-audit-pack", "code_analysis",
                         code=code, severity="LOW")
    security_findings = security if ok else {}

    # Step 3: Secret scanning
    context.next_iteration()
    ok, secrets = _call(context, "secret-scanner-pack", "code_analysis", code=code)
    secrets_findings = secrets if ok else {}

    # Step 4: Search for known issues
    context.next_iteration()
    # Extract imports to check for vulnerable packages
    import_lines = [l.strip() for l in code.split("\\n")
                    if l.strip().startswith("import ") or l.strip().startswith("from ")]
    vuln_info = []
    if import_lines:
        pkgs = " ".join(import_lines[:5])
        ok, search = _call(context, "web-search-pack", "search_web",
                           query=f"python security vulnerability {pkgs[:100]}",
                           max_results=3)
        if ok:
            vuln_info = search.get("results", [])

    # Severity breakdown
    total_issues = 0
    for findings in [lint_findings, security_findings, secrets_findings]:
        if isinstance(findings, dict):
            issues = findings.get("issues", findings.get("findings", []))
            if isinstance(issues, list):
                total_issues += len(issues)

    return {"scan_results": {"lint": lint_findings, "security": security_findings,
                             "secrets": secrets_findings},
            "known_vulnerabilities": [{"title": v.get("title", ""), "url": v.get("url", "")}
                                      for v in vuln_info],
            "total_issues": total_issues,
            "done": True}
""",
    },
    {
        "slug": "cloud-cost-agent",
        "name": "Cloud Cost Agent",
        "summary": "Analyze cloud infrastructure costs, identify waste, and recommend optimizations.",
        "goal": "Analyze cloud costs by examining billing data and recommending optimizations.",
        "tags": ["agent", "cloud", "cost", "optimization", "devops"],
        "category": "ops",
        "permissions": {"network": "restricted", "filesystem": "workspace_read"},
        "run_code": """\
    file_path = kwargs.get("file_path", "") or context.goal

    # Step 1: Describe the billing data
    context.next_iteration()
    ok, desc = _call(context, "csv-analyzer-pack", "describe_csv", file_path=file_path)
    billing_stats = desc if ok else {"error": "Could not read billing data"}

    # Step 2: Get column info
    context.next_iteration()
    ok, cols = _call(context, "csv-analyzer-pack", "columns_csv", file_path=file_path)
    columns = cols if ok else {}

    # Step 3: Sample the data
    context.next_iteration()
    ok, head = _call(context, "csv-analyzer-pack", "head_csv", file_path=file_path, n=15)
    sample = head if ok else {}

    # Step 4: Search for cost optimization best practices
    context.next_iteration()
    ok, tips = _call(context, "web-search-pack", "search_web",
                     query="cloud cost optimization best practices 2026",
                     max_results=5)
    optimization_tips = [r.get("title", "") for r in tips.get("results", [])] if ok else []

    # Step 5: Summarize findings
    context.next_iteration()
    analysis_text = f"Billing data: {file_path}\\nStats: {billing_stats}\\nColumns: {columns}"
    ok, summary = _call(context, "document-summarizer-pack", "document_summary",
                        text=analysis_text, max_sentences=6)

    return {"cost_analysis": summary.get("summary", analysis_text[:500]) if ok else analysis_text[:500],
            "billing_statistics": billing_stats,
            "columns": columns, "sample_data": sample,
            "optimization_tips": optimization_tips,
            "file": file_path, "done": True}
""",
    },
    {
        "slug": "deployment-agent",
        "name": "Deployment Agent",
        "summary": "Orchestrate deployments: verify code quality, run checks, and produce a deployment checklist.",
        "goal": "Deploy by verifying build quality, running tests, and producing a deployment checklist.",
        "tags": ["agent", "deployment", "devops", "docker", "cloud"],
        "category": "ops",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read", "code_execution": "limited_subprocess"},
        "run_code": """\
    code = kwargs.get("code", "") or context.goal

    # Step 1: Lint code quality
    context.next_iteration()
    ok, lint = _call(context, "code-linter-pack", "code_analysis",
                     code=code, language="python")
    lint_ok = ok and not lint.get("issues", [])
    lint_result = lint if ok else {"error": "Lint failed"}

    # Step 2: Security audit
    context.next_iteration()
    ok, security = _call(context, "security-audit-pack", "code_analysis",
                         code=code, severity="MEDIUM")
    security_ok = ok and not security.get("issues", [])
    security_result = security if ok else {"error": "Security audit failed"}

    # Step 3: Secret scan
    context.next_iteration()
    ok, secrets = _call(context, "secret-scanner-pack", "code_analysis", code=code)
    secrets_ok = ok and not secrets.get("findings", secrets.get("secrets", []))
    secrets_result = secrets if ok else {}

    # Step 4: Generate test status
    context.next_iteration()
    ok, tests = _call(context, "test-generator-pack", "code_analysis",
                      code=code, framework="pytest")
    tests_result = tests if ok else {}

    # Build deployment checklist
    checks = [
        {"check": "Code linting", "passed": lint_ok},
        {"check": "Security audit", "passed": security_ok},
        {"check": "Secret scanning", "passed": secrets_ok},
        {"check": "Test generation", "passed": bool(tests_result.get("tests", tests_result.get("output", "")))},
    ]
    all_passed = all(c["passed"] for c in checks)

    return {"ready_to_deploy": all_passed,
            "checklist": checks,
            "lint": lint_result, "security": security_result,
            "secrets": secrets_result, "tests": tests_result,
            "done": True}
""",
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# Code generators
# ═══════════════════════════════════════════════════════════════════════════

def _module_name(slug: str) -> str:
    return slug.replace("-", "_")


def _class_name(slug: str) -> str:
    return "".join(w.capitalize() for w in slug.replace("-", " ").split())


def _generate_agent_code(agent: dict) -> str:
    mod = _module_name(agent["slug"])
    tier = agent.get("tier", "")
    is_llm_only = tier == "llm_only"
    version = "v3" if is_llm_only else "v2"

    lines = []
    lines.append(f'"""{mod} — AgentNode agent {version}')
    lines.append(f'')
    lines.append(f'{agent["name"]}: {agent["summary"]}')
    lines.append(f'"""')
    lines.append(f'from __future__ import annotations')
    lines.append(f'')
    lines.append(f'import logging')
    lines.append(f'from typing import Any')
    lines.append(f'')
    lines.append(f'logger = logging.getLogger(__name__)')
    lines.append(f'')
    lines.append(f'')

    # Only include _HELPER for tool-using agents
    if not is_llm_only:
        lines.append(_HELPER)
        lines.append(f'')

    lines.append(f'def run(context: Any, **kwargs: Any) -> dict:')
    if is_llm_only:
        lines.append(f'    """Agent entrypoint — LLM-only agent (tier: llm_only).')
        lines.append(f'')
        lines.append(f'    Uses context.call_llm_text() for LLM reasoning.')
        lines.append(f'    System prompt is injected automatically from the manifest.')
    else:
        lines.append(f'    """Agent entrypoint — AgentContext contract v1.')
        lines.append(f'')
        lines.append(f'    Uses context.run_tool() for tool access.')
    lines.append(f'')
    lines.append(f'    Args:')
    lines.append(f'        context: AgentContext with goal and LLM/tool access.')
    lines.append(f'        **kwargs: Additional parameters from the caller.')
    lines.append(f'')
    lines.append(f'    Returns:')
    lines.append(f'        Structured result dict.')
    lines.append(f'    """')
    lines.append(agent["run_code"])
    return "\n".join(lines)


def _generate_manifest(agent: dict) -> str:
    mod = _module_name(agent["slug"])
    perms = agent.get("permissions", {})
    net = perms.get("network", "none")
    fs = perms.get("filesystem", "none")
    code_exec = perms.get("code_execution", "none")
    tags_str = ", ".join(f'"{t}"' for t in agent["tags"])

    tier = agent.get("tier", "")
    is_llm_only = tier == "llm_only"
    version = "3.0.0" if is_llm_only else "2.0.0"
    system_prompt = agent.get("system_prompt", "")

    if is_llm_only:
        description_detail = (
            "  LLM-only agent (tier: llm_only). Uses the user's LLM for reasoning.\n"
            "  System prompt defines the agent's role and behavior.\n"
            "  No external tools or data sources required."
        )
    else:
        description_detail = (
            "  Uses AgentContext contract v1: calls tools via context.run_tool()\n"
            "  with real data flow between steps and structured output synthesis."
        )

    # Agent section
    agent_section = f'''\
agent:
  entrypoint: "{mod}.agent:run"
  goal: "{agent["goal"][:250]}"'''

    if tier:
        agent_section += f'\n  tier: "{tier}"'

    if system_prompt:
        # Indent the system prompt for YAML block scalar
        sp_lines = system_prompt.strip().split("\n")
        sp_indented = "\n    ".join(sp_lines)
        agent_section += f'\n  system_prompt: |\n    {sp_indented}'

    if is_llm_only:
        agent_section += '''
  llm:
    required: true
  tool_access:
    allowed_packages: []
  limits:
    max_iterations: 10
    max_tool_calls: 0
    max_runtime_seconds: 300
  termination:
    stop_on_final_answer: true
    stop_on_consecutive_errors: 3
  isolation: "thread"'''
    else:
        agent_section += '''
  tool_access:
    allowed_packages: []
  limits:
    max_iterations: 10
    max_tool_calls: 50
    max_runtime_seconds: 300
  termination:
    stop_on_final_answer: true
    stop_on_consecutive_errors: 3
  isolation: "thread"
  state:
    persistence: "none"'''

    return f'''\
manifest_version: "0.2"
package_id: "{agent["slug"]}"
package_type: "agent"
name: "{agent["name"]}"
publisher: "agentnode"
version: "{version}"
summary: "{agent["summary"][:200]}"
description: |
  {agent["goal"]}

{description_detail}

runtime: "python"
install_mode: "package"
hosting_type: "agentnode_hosted"
entrypoint: "{mod}.agent"

capabilities:
  tools:
    - name: "{mod.replace('_agent', '')}"
      capability_id: "{mod.replace('_agent', '')}"
      description: "{agent["summary"][:200]}"
      entrypoint: "{mod}.agent:run"
      input_schema:
        type: "object"
        properties:
          goal:
            type: "string"
            description: "The objective for the agent"
        required: ["goal"]
      output_schema:
        type: "object"
        properties:
          result:
            type: "object"
          done:
            type: "boolean"
  resources: []
  prompts: []

{agent_section}

compatibility:
  frameworks: ["generic"]
  python: ">=3.10"
  dependencies: []

permissions:
  network:
    level: "{net}"
  filesystem:
    level: "{fs}"
  code_execution:
    level: "{code_exec}"
  data_access:
    level: "input_only"
  user_approval:
    required: "never"

tags: [{tags_str}]
categories: ["{agent["category"]}"]
'''


def _generate_init(agent: dict) -> str:
    return f'"""AgentNode agent package: {agent["name"]}"""\n'


def _generate_pyproject(agent: dict) -> str:
    tier = agent.get("tier", "")
    version = "3.0.0" if tier == "llm_only" else "2.0.0"
    return f'''\
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "{agent["slug"]}"
version = "{version}"
description = "{agent["summary"][:120]}"
requires-python = ">=3.10"
dependencies = []

[tool.setuptools.packages.find]
where = ["src"]
'''


def main():
    base = os.path.join(os.path.dirname(__file__), "..", "starter-packs")
    created = 0

    for agent in AGENTS:
        slug = agent["slug"]
        mod = _module_name(slug)
        pack_dir = os.path.join(base, slug)
        src_dir = os.path.join(pack_dir, "src", mod)

        os.makedirs(src_dir, exist_ok=True)

        # agentnode.yaml
        with open(os.path.join(pack_dir, "agentnode.yaml"), "w", encoding="utf-8") as f:
            f.write(_generate_manifest(agent))

        # src/<module>/agent.py
        with open(os.path.join(src_dir, "agent.py"), "w", encoding="utf-8") as f:
            f.write(_generate_agent_code(agent))

        # src/<module>/__init__.py
        with open(os.path.join(src_dir, "__init__.py"), "w", encoding="utf-8") as f:
            f.write(_generate_init(agent))

        # pyproject.toml
        with open(os.path.join(pack_dir, "pyproject.toml"), "w", encoding="utf-8") as f:
            f.write(_generate_pyproject(agent))

        created += 1
        print(f"  [{created:2d}/30] {slug}")

    print(f"\nDone: {created} agent packs generated in starter-packs/")


if __name__ == "__main__":
    main()
