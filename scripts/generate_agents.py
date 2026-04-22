#!/usr/bin/env python3
"""Generate 30 first-party agent starter-packs for AgentNode.

Each agent uses allowed_packages: [] (full registry access) and the
detect_and_install + smart_run SDK pattern.
"""

import os
import textwrap

AGENTS = [
    # --- Research & Analysis (6) ---
    {
        "slug": "deep-research-agent",
        "name": "Deep Research Agent",
        "summary": "Conduct deep multi-source research on any topic, synthesize findings from web and documents into a structured report.",
        "goal": "Research a topic thoroughly using web search, webpage extraction, and document summarization, then produce a structured report with sources and key findings.",
        "capabilities": ["web_search", "pdf_extraction", "document_summary"],
        "capability_id": "deep_research",
        "tags": ["agent", "research", "analysis"],
        "category": "research",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read"},
        "steps": [
            ("web_search", "query", "Search the web for the research topic"),
            ("webpage_extraction", "urls", "Extract full content from top results"),
            ("document_summary", "text", "Synthesize and summarize all findings"),
        ],
    },
    {
        "slug": "academic-research-agent",
        "name": "Academic Research Agent",
        "summary": "Search academic papers on arXiv and Google Scholar, extract citations, and produce a literature review.",
        "goal": "Conduct academic literature research by searching paper databases, extracting PDFs, collecting citations, and summarizing findings into a literature review.",
        "capabilities": ["web_search", "pdf_extraction", "citation_extraction", "document_summary"],
        "capability_id": "academic_research",
        "tags": ["agent", "research", "academic", "papers"],
        "category": "research",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read"},
        "steps": [
            ("web_search", "query", "Search academic databases for papers"),
            ("pdf_extraction", "file_path", "Extract text from downloaded papers"),
            ("citation_extraction", "text", "Extract citations and references"),
            ("document_summary", "text", "Summarize findings into a literature review"),
        ],
    },
    {
        "slug": "competitive-intel-agent",
        "name": "Competitive Intelligence Agent",
        "summary": "Analyze competitors by scraping their web presence, monitoring news, and producing a competitive analysis report.",
        "goal": "Gather competitive intelligence by searching the web, extracting content from competitor pages, aggregating news, and summarizing into a competitive analysis.",
        "capabilities": ["web_search", "webpage_extraction", "document_summary"],
        "capability_id": "competitive_intelligence",
        "tags": ["agent", "research", "competitive", "business"],
        "category": "research",
        "permissions": {"network": "unrestricted", "filesystem": "none"},
        "steps": [
            ("web_search", "company", "Search for competitor information"),
            ("webpage_extraction", "urls", "Extract content from competitor websites"),
            ("document_summary", "text", "Produce competitive analysis report"),
        ],
    },
    {
        "slug": "seo-research-agent",
        "name": "SEO Research Agent",
        "summary": "Audit a website's SEO by analyzing content, keywords, meta tags, and competitor rankings.",
        "goal": "Perform SEO analysis on a target URL by extracting page content, analyzing keywords and meta tags, checking competitor rankings, and producing an SEO audit report.",
        "capabilities": ["web_search", "webpage_extraction"],
        "capability_id": "seo_research",
        "tags": ["agent", "seo", "marketing", "analysis"],
        "category": "research",
        "permissions": {"network": "unrestricted", "filesystem": "none"},
        "steps": [
            ("webpage_extraction", "url", "Extract page content and meta tags"),
            ("web_search", "keywords", "Check keyword rankings and competitors"),
            ("document_summary", "text", "Produce SEO audit report"),
        ],
    },
    {
        "slug": "fact-check-agent",
        "name": "Fact Check Agent",
        "summary": "Verify claims against multiple web sources and produce a fact-check verdict with supporting evidence.",
        "goal": "Verify a given claim by searching multiple sources, extracting relevant evidence, cross-referencing facts, and producing a verdict with confidence level.",
        "capabilities": ["web_search", "webpage_extraction", "semantic_search"],
        "capability_id": "fact_checking",
        "tags": ["agent", "fact-check", "verification", "research"],
        "category": "research",
        "permissions": {"network": "unrestricted", "filesystem": "none"},
        "steps": [
            ("web_search", "claim", "Search for evidence supporting and contradicting the claim"),
            ("webpage_extraction", "urls", "Extract full content from source pages"),
            ("semantic_search", "query", "Find semantically relevant evidence"),
        ],
    },
    {
        "slug": "news-digest-agent",
        "name": "News Digest Agent",
        "summary": "Aggregate news from multiple sources on a topic, summarize key stories, and optionally translate for multilingual digests.",
        "goal": "Create a news digest by aggregating articles from multiple sources, summarizing key stories, and optionally translating content for multilingual audiences.",
        "capabilities": ["web_search", "document_summary", "translation"],
        "capability_id": "news_digest",
        "tags": ["agent", "news", "digest", "multilingual"],
        "category": "research",
        "permissions": {"network": "unrestricted", "filesystem": "none"},
        "steps": [
            ("web_search", "topic", "Search for recent news on the topic"),
            ("webpage_extraction", "urls", "Extract article content"),
            ("document_summary", "text", "Summarize each article"),
            ("translation", "text", "Translate digest if target language specified"),
        ],
    },
    # --- Content Creation (5) ---
    {
        "slug": "blog-writer-agent",
        "name": "Blog Writer Agent",
        "summary": "Research a topic and write an SEO-optimized blog post with proper structure, keywords, and meta description.",
        "goal": "Write a complete blog post by researching the topic, structuring an outline, writing SEO-optimized content with keywords, and generating a meta description.",
        "capabilities": ["web_search", "webpage_extraction", "document_summary"],
        "capability_id": "blog_writing",
        "tags": ["agent", "content", "blog", "seo", "writing"],
        "category": "content",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_write"},
        "steps": [
            ("web_search", "topic", "Research the blog topic"),
            ("webpage_extraction", "urls", "Analyze top-ranking articles for structure"),
            ("tone_adjustment", "text", "Write and polish the blog post"),
        ],
    },
    {
        "slug": "technical-docs-agent",
        "name": "Technical Documentation Agent",
        "summary": "Generate API documentation and developer guides from source code, including examples and type signatures.",
        "goal": "Generate comprehensive technical documentation from source code by analyzing code structure, extracting docstrings and type signatures, and producing formatted developer guides.",
        "capabilities": ["code_analysis", "document_parsing"],
        "capability_id": "technical_documentation",
        "tags": ["agent", "docs", "api", "code", "developer"],
        "category": "content",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "steps": [
            ("code_analysis", "file_path", "Analyze source code structure and signatures"),
            ("document_parsing", "text", "Parse existing documentation"),
            ("tone_adjustment", "text", "Format and polish documentation"),
        ],
    },
    {
        "slug": "newsletter-agent",
        "name": "Newsletter Agent",
        "summary": "Curate top stories on a topic, summarize them, and draft a ready-to-send newsletter email.",
        "goal": "Create a newsletter by aggregating top stories on a topic, summarizing each article, and drafting a formatted newsletter email ready for distribution.",
        "capabilities": ["web_search", "document_summary", "email_drafting"],
        "capability_id": "newsletter_creation",
        "tags": ["agent", "newsletter", "email", "content", "curation"],
        "category": "content",
        "permissions": {"network": "unrestricted", "filesystem": "none"},
        "steps": [
            ("web_search", "topic", "Find top stories on the topic"),
            ("document_summary", "text", "Summarize each story"),
            ("email_drafting", "content", "Draft the newsletter email"),
        ],
    },
    {
        "slug": "social-media-agent",
        "name": "Social Media Agent",
        "summary": "Create platform-optimized social media posts with copy, hashtags, and image suggestions from a topic or URL.",
        "goal": "Generate social media content by researching the topic, writing platform-optimized copy with hashtags, and suggesting or generating accompanying images.",
        "capabilities": ["web_search", "webpage_extraction"],
        "capability_id": "social_media_content",
        "tags": ["agent", "social-media", "content", "marketing"],
        "category": "content",
        "permissions": {"network": "unrestricted", "filesystem": "none"},
        "steps": [
            ("web_search", "topic", "Research trending angles on the topic"),
            ("webpage_extraction", "url", "Extract content from source URL"),
            ("tone_adjustment", "text", "Write platform-optimized copy"),
        ],
    },
    {
        "slug": "report-generator-agent",
        "name": "Report Generator Agent",
        "summary": "Transform raw data (CSV, JSON) into a formatted report with charts, tables, and executive summary.",
        "goal": "Generate a business report from raw data by analyzing datasets, creating visualizations, computing statistics, and writing an executive summary.",
        "capabilities": ["csv_analysis", "statistics_analysis", "chart_generation", "document_summary"],
        "capability_id": "report_generation",
        "tags": ["agent", "report", "data", "visualization", "analytics"],
        "category": "content",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "steps": [
            ("csv_analysis", "file_path", "Load and analyze the dataset"),
            ("statistics_analysis", "data", "Compute summary statistics"),
            ("chart_generation", "data", "Generate charts and visualizations"),
            ("document_summary", "text", "Write executive summary"),
        ],
    },
    # --- Data & Analytics (5) ---
    {
        "slug": "csv-analyst-agent",
        "name": "CSV Analyst Agent",
        "summary": "Upload a CSV, detect patterns and anomalies, generate visualizations, and produce an analysis report.",
        "goal": "Analyze a CSV file by detecting data types, finding patterns and anomalies, generating visualizations, and producing a structured analysis report.",
        "capabilities": ["csv_analysis", "statistics_analysis", "chart_generation"],
        "capability_id": "csv_analysis_workflow",
        "tags": ["agent", "csv", "data", "analytics", "visualization"],
        "category": "data",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "steps": [
            ("csv_analysis", "file_path", "Parse CSV and detect column types"),
            ("statistics_analysis", "data", "Compute statistics and find patterns"),
            ("chart_generation", "data", "Generate charts for key metrics"),
        ],
    },
    {
        "slug": "log-investigator-agent",
        "name": "Log Investigator Agent",
        "summary": "Parse log files, identify errors and anomalies, correlate events, and produce an incident report.",
        "goal": "Investigate log files by parsing entries, identifying error patterns and anomalies, correlating events across sources, and generating an incident investigation report.",
        "capabilities": ["log_analysis", "json_processing", "document_summary"],
        "capability_id": "log_investigation",
        "tags": ["agent", "logs", "debugging", "incident", "devops"],
        "category": "data",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "steps": [
            ("log_analysis", "file_path", "Parse and analyze log entries"),
            ("json_processing", "data", "Structure and correlate log events"),
            ("document_summary", "text", "Generate incident investigation report"),
        ],
    },
    {
        "slug": "data-pipeline-agent",
        "name": "Data Pipeline Agent",
        "summary": "Build and run a data pipeline: load from CSV/JSON, clean and transform, then output to a target format.",
        "goal": "Execute a data pipeline by loading data from source files, cleaning and transforming it according to rules, and writing the results to the target format or database.",
        "capabilities": ["csv_analysis", "json_processing", "data_cleaning"],
        "capability_id": "data_pipeline",
        "tags": ["agent", "etl", "data", "pipeline", "transformation"],
        "category": "data",
        "permissions": {"network": "restricted", "filesystem": "workspace_write"},
        "steps": [
            ("csv_analysis", "file_path", "Load source data"),
            ("data_cleaning", "data", "Clean and normalize data"),
            ("json_processing", "data", "Transform to target format"),
        ],
    },
    {
        "slug": "spreadsheet-auditor-agent",
        "name": "Spreadsheet Auditor Agent",
        "summary": "Audit Excel/CSV spreadsheets for errors, duplicates, formula issues, and data inconsistencies.",
        "goal": "Audit a spreadsheet by checking for duplicates, data type mismatches, formula errors, missing values, and inconsistencies, then produce an audit report with fix suggestions.",
        "capabilities": ["spreadsheet_parsing", "csv_analysis", "data_cleaning"],
        "capability_id": "spreadsheet_audit",
        "tags": ["agent", "spreadsheet", "audit", "excel", "quality"],
        "category": "data",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "steps": [
            ("spreadsheet_parsing", "file_path", "Parse the spreadsheet"),
            ("data_cleaning", "data", "Identify errors and inconsistencies"),
            ("csv_analysis", "data", "Compute quality metrics"),
        ],
    },
    {
        "slug": "sql-report-agent",
        "name": "SQL Report Agent",
        "summary": "Answer natural language questions about a database by generating SQL queries, executing them, and visualizing results.",
        "goal": "Answer data questions by translating natural language to SQL, executing queries against a database, analyzing results, and generating visualizations and a summary report.",
        "capabilities": ["sql_generation", "csv_analysis", "chart_generation"],
        "capability_id": "sql_reporting",
        "tags": ["agent", "sql", "database", "report", "analytics"],
        "category": "data",
        "permissions": {"network": "restricted", "filesystem": "none"},
        "steps": [
            ("sql_generation", "question", "Generate SQL from natural language"),
            ("csv_analysis", "data", "Analyze query results"),
            ("chart_generation", "data", "Visualize results"),
        ],
    },
    # --- Development (5) ---
    {
        "slug": "code-review-agent",
        "name": "Code Review Agent",
        "summary": "Perform comprehensive code review: lint, security audit, refactoring suggestions, and best practices check.",
        "goal": "Review code by running linting, checking for security vulnerabilities, suggesting refactorings, and verifying adherence to best practices, then produce a review report.",
        "capabilities": ["code_analysis"],
        "capability_id": "code_review",
        "tags": ["agent", "code-review", "security", "quality", "developer"],
        "category": "development",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "steps": [
            ("code_analysis", "file_path", "Analyze code structure and quality"),
            ("code_analysis", "file_path", "Check for security vulnerabilities"),
            ("code_analysis", "file_path", "Suggest refactorings"),
        ],
    },
    {
        "slug": "test-writer-agent",
        "name": "Test Writer Agent",
        "summary": "Analyze source code and generate comprehensive test suites with unit tests, edge cases, and mocks.",
        "goal": "Generate tests for source code by analyzing function signatures and logic, identifying edge cases, creating unit tests with assertions, and validating they compile.",
        "capabilities": ["code_analysis"],
        "capability_id": "test_generation",
        "tags": ["agent", "testing", "code", "quality", "developer"],
        "category": "development",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "steps": [
            ("code_analysis", "file_path", "Analyze source code to understand functions"),
            ("code_analysis", "file_path", "Generate test cases for each function"),
            ("code_analysis", "file_path", "Validate generated tests compile"),
        ],
    },
    {
        "slug": "dependency-audit-agent",
        "name": "Dependency Audit Agent",
        "summary": "Scan project dependencies for known vulnerabilities, outdated versions, license issues, and leaked secrets.",
        "goal": "Audit project dependencies by scanning for known CVEs, checking for outdated packages, verifying license compatibility, and detecting leaked secrets in configuration.",
        "capabilities": ["code_analysis", "web_search"],
        "capability_id": "dependency_audit",
        "tags": ["agent", "security", "dependencies", "audit", "devops"],
        "category": "development",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read"},
        "steps": [
            ("code_analysis", "file_path", "Parse dependency files"),
            ("web_search", "package", "Check for known vulnerabilities"),
            ("code_analysis", "file_path", "Scan for leaked secrets"),
        ],
    },
    {
        "slug": "ci-cd-agent",
        "name": "CI/CD Agent",
        "summary": "Set up and manage CI/CD pipelines: configure builds, run tests, build containers, and deploy.",
        "goal": "Orchestrate a CI/CD pipeline by configuring build steps, running tests, building Docker containers, pushing images, and triggering deployments.",
        "capabilities": ["code_analysis"],
        "capability_id": "cicd_orchestration",
        "tags": ["agent", "cicd", "devops", "deployment", "docker"],
        "category": "development",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read", "code_execution": "limited_subprocess"},
        "steps": [
            ("code_analysis", "file_path", "Analyze project structure for build config"),
            ("code_analysis", "file_path", "Run test suite"),
            ("code_analysis", "file_path", "Build and deploy"),
        ],
    },
    {
        "slug": "api-design-agent",
        "name": "API Design Agent",
        "summary": "Generate an OpenAPI specification from requirements, validate it, and produce API documentation.",
        "goal": "Design an API by converting requirements into an OpenAPI spec, validating the schema, generating documentation with examples, and checking for REST best practices.",
        "capabilities": ["code_analysis", "json_processing"],
        "capability_id": "api_design",
        "tags": ["agent", "api", "openapi", "design", "developer"],
        "category": "development",
        "permissions": {"network": "none", "filesystem": "workspace_write"},
        "steps": [
            ("json_processing", "requirements", "Generate OpenAPI schema from requirements"),
            ("code_analysis", "schema", "Validate OpenAPI spec"),
            ("json_processing", "schema", "Generate API documentation"),
        ],
    },
    # --- Business & Productivity (5) ---
    {
        "slug": "email-triage-agent",
        "name": "Email Triage Agent",
        "summary": "Prioritize incoming emails, draft responses for routine messages, and create tasks from action items.",
        "goal": "Triage emails by categorizing priority, drafting responses for routine messages, extracting action items, and creating tasks in the task management system.",
        "capabilities": ["email_summary", "email_drafting", "task_management"],
        "capability_id": "email_triage",
        "tags": ["agent", "email", "productivity", "triage"],
        "category": "productivity",
        "permissions": {"network": "restricted", "filesystem": "none"},
        "steps": [
            ("email_summary", "emails", "Summarize and categorize emails by priority"),
            ("email_drafting", "context", "Draft responses for routine emails"),
            ("task_management", "items", "Create tasks from action items"),
        ],
    },
    {
        "slug": "meeting-prep-agent",
        "name": "Meeting Prep Agent",
        "summary": "Prepare for meetings by researching attendees, summarizing relevant docs, and generating an agenda with talking points.",
        "goal": "Prepare for a meeting by researching attendees and topics, summarizing relevant documents, and generating a structured agenda with talking points and action items.",
        "capabilities": ["web_search", "document_summary", "scheduling"],
        "capability_id": "meeting_preparation",
        "tags": ["agent", "meeting", "productivity", "preparation"],
        "category": "productivity",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read"},
        "steps": [
            ("web_search", "attendees", "Research meeting attendees and topics"),
            ("document_summary", "text", "Summarize relevant documents"),
            ("scheduling", "meeting", "Generate agenda and talking points"),
        ],
    },
    {
        "slug": "project-planner-agent",
        "name": "Project Planner Agent",
        "summary": "Break down a project goal into user stories, tasks, and milestones with time estimates.",
        "goal": "Plan a project by decomposing the goal into user stories with acceptance criteria, breaking stories into tasks, estimating effort, and organizing into milestones.",
        "capabilities": ["task_management"],
        "capability_id": "project_planning",
        "tags": ["agent", "project", "planning", "agile", "productivity"],
        "category": "productivity",
        "permissions": {"network": "none", "filesystem": "workspace_write"},
        "steps": [
            ("task_management", "goal", "Decompose project into user stories"),
            ("task_management", "stories", "Break stories into tasks with estimates"),
            ("task_management", "tasks", "Organize into milestones"),
        ],
    },
    {
        "slug": "contract-review-agent",
        "name": "Contract Review Agent",
        "summary": "Analyze legal contracts, flag risky clauses, compare against templates, and suggest amendments.",
        "goal": "Review a legal contract by extracting key clauses, identifying risks and unusual terms, comparing against standard templates, and suggesting amendments.",
        "capabilities": ["pdf_extraction", "document_parsing", "document_summary"],
        "capability_id": "contract_review",
        "tags": ["agent", "legal", "contract", "review", "compliance"],
        "category": "productivity",
        "permissions": {"network": "none", "filesystem": "workspace_read"},
        "steps": [
            ("pdf_extraction", "file_path", "Extract contract text from PDF"),
            ("document_parsing", "text", "Parse and identify key clauses"),
            ("document_summary", "text", "Summarize risks and suggest amendments"),
        ],
    },
    {
        "slug": "crm-enrichment-agent",
        "name": "CRM Enrichment Agent",
        "summary": "Enrich CRM contacts with public web data: company info, social profiles, recent news, and role details.",
        "goal": "Enrich CRM contact records by searching the web for company information, social profiles, recent news mentions, and role details, then update the CRM records.",
        "capabilities": ["web_search", "webpage_extraction"],
        "capability_id": "crm_enrichment",
        "tags": ["agent", "crm", "sales", "enrichment", "business"],
        "category": "productivity",
        "permissions": {"network": "unrestricted", "filesystem": "none"},
        "steps": [
            ("web_search", "contact", "Search for contact and company information"),
            ("webpage_extraction", "urls", "Extract detailed profile data"),
            ("web_search", "company", "Find recent news and updates"),
        ],
    },
    # --- Monitoring & Ops (4) ---
    {
        "slug": "website-monitor-agent",
        "name": "Website Monitor Agent",
        "summary": "Monitor websites for content changes, downtime, and visual regressions, and send alerts via Slack or email.",
        "goal": "Monitor target websites by periodically checking for content changes, downtime, and visual regressions, then send alerts with diff details via configured channels.",
        "capabilities": ["webpage_extraction", "document_summary"],
        "capability_id": "website_monitoring",
        "tags": ["agent", "monitoring", "website", "alerts", "devops"],
        "category": "ops",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_write"},
        "steps": [
            ("webpage_extraction", "url", "Fetch current page content"),
            ("document_summary", "text", "Compare with previous snapshot"),
            ("web_search", "url", "Check uptime and response time"),
        ],
    },
    {
        "slug": "security-scanner-agent",
        "name": "Security Scanner Agent",
        "summary": "Run a comprehensive security scan on a codebase: SAST, dependency vulnerabilities, secret detection, and compliance check.",
        "goal": "Perform a security scan by running static analysis, checking dependencies for CVEs, detecting leaked secrets, verifying compliance, and producing a consolidated security report.",
        "capabilities": ["code_analysis", "web_search"],
        "capability_id": "security_scanning",
        "tags": ["agent", "security", "scanning", "compliance", "devops"],
        "category": "ops",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read"},
        "steps": [
            ("code_analysis", "file_path", "Run static analysis (SAST)"),
            ("web_search", "dependencies", "Check for known CVEs"),
            ("code_analysis", "file_path", "Detect secrets and credentials"),
        ],
    },
    {
        "slug": "cloud-cost-agent",
        "name": "Cloud Cost Agent",
        "summary": "Analyze cloud infrastructure costs across AWS/Azure, identify waste, and recommend optimization strategies.",
        "goal": "Analyze cloud costs by fetching billing data, identifying underutilized resources and waste, benchmarking against best practices, and recommending cost optimizations.",
        "capabilities": ["csv_analysis", "chart_generation", "document_summary"],
        "capability_id": "cloud_cost_optimization",
        "tags": ["agent", "cloud", "cost", "optimization", "devops"],
        "category": "ops",
        "permissions": {"network": "restricted", "filesystem": "workspace_read"},
        "steps": [
            ("csv_analysis", "data", "Analyze billing and usage data"),
            ("chart_generation", "data", "Visualize cost trends and breakdown"),
            ("document_summary", "text", "Generate cost optimization report"),
        ],
    },
    {
        "slug": "deployment-agent",
        "name": "Deployment Agent",
        "summary": "Orchestrate application deployments: build, test, push images, deploy to cloud, verify health, and notify the team.",
        "goal": "Deploy an application by building the project, running tests, pushing container images, deploying to the target environment, verifying health checks, and sending team notifications.",
        "capabilities": ["code_analysis"],
        "capability_id": "deployment_orchestration",
        "tags": ["agent", "deployment", "devops", "docker", "cloud"],
        "category": "ops",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read", "code_execution": "limited_subprocess"},
        "steps": [
            ("code_analysis", "file_path", "Verify build and test status"),
            ("code_analysis", "file_path", "Build and push container image"),
            ("web_search", "endpoint", "Verify deployment health"),
        ],
    },
]


def _module_name(slug: str) -> str:
    return slug.replace("-", "_")


def _generate_manifest(agent: dict) -> str:
    mod = _module_name(agent["slug"])
    perms = agent.get("permissions", {})
    net = perms.get("network", "none")
    fs = perms.get("filesystem", "none")
    code_exec = perms.get("code_execution", "none")
    tags_str = ", ".join(f'"{t}"' for t in agent["tags"])

    return f'''\
manifest_version: "0.2"
package_id: "{agent["slug"]}"
package_type: "agent"
name: "{agent["name"]}"
publisher: "agentnode"
version: "1.0.0"
summary: "{agent["summary"][:200]}"
description: |
  {agent["goal"]}

  This agent uses AgentNode's full skill registry to dynamically discover
  and install the capabilities it needs at runtime.

runtime: "python"
install_mode: "package"
hosting_type: "agentnode_hosted"
entrypoint: "{mod}.agent"

capabilities:
  tools:
    - name: "{agent["capability_id"]}"
      capability_id: "{agent["capability_id"]}"
      description: "{agent["summary"][:200]}"
      entrypoint: "{mod}.agent:run"
      input_schema:
        type: "object"
        properties:
          goal:
            type: "string"
            description: "The objective for the agent"
          context:
            type: "object"
            description: "Optional context and parameters"
        required: ["goal"]
      output_schema:
        type: "object"
        properties:
          result:
            type: "object"
          done:
            type: "boolean"
          error:
            type: "string"
  resources: []
  prompts: []

agent:
  entrypoint: "{mod}.agent:run"
  goal: "{agent["goal"][:250]}"
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
    persistence: "none"

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


def _generate_agent_code(agent: dict) -> str:
    mod = _module_name(agent["slug"])

    # Build STEPS list literal
    steps_items = []
    for cap, param, desc in agent["steps"]:
        steps_items.append(f'        ("{cap}", "{param}", "{desc}"),')
    steps_literal = "\n".join(steps_items)

    return f'''\
"""{mod} — AgentNode agent (ANP v0.2)

{agent["name"]}: {agent["summary"]}
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Workflow steps: (capability_id, param_key, description)
STEPS = [
{steps_literal}
]


class {_class_name(agent["slug"])}:
    """
    {agent["goal"][:200]}

    Uses AgentNode SDK's detect_and_install + run_tool pattern to dynamically
    discover and use capabilities from the full skill registry.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("AGENTNODE_API_KEY", "")

    async def execute(self, goal: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run the agent workflow.

        Args:
            goal: The objective to accomplish.
            context: Optional parameters and context.

        Returns:
            Dict with result, done status, and metadata.
        """
        findings: list[dict[str, Any]] = []
        consecutive_errors = 0

        try:
            from agentnode_sdk import AgentNodeClient
            client = AgentNodeClient(api_key=self._api_key)
        except ImportError:
            logger.warning("agentnode_sdk not installed, returning stub result")
            return {{"result": None, "done": False, "error": "agentnode_sdk not installed"}}

        try:
            for capability, param_key, description in STEPS:
                step_result = await self._use_capability(client, capability, {{
                    param_key: goal,
                    **(context or {{}}),
                }})
                findings.append({{"step": description, "result": step_result}})
                if step_result.get("error"):
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        break
                else:
                    consecutive_errors = 0
        finally:
            client.close()

        return {{
            "result": findings,
            "done": True,
            "goal": goal,
            "steps_completed": len(findings),
        }}

    async def _use_capability(
        self,
        client: Any,
        capability: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Use a capability via smart_run with auto-detection and install."""
        try:
            result = client.smart_run(
                lambda: client.run_tool(capability, **params),
                auto_upgrade_policy="safe",
            )
            if result.success:
                return result.result if isinstance(result.result, dict) else {{"output": result.result}}
            return {{"error": result.error or "Unknown error"}}
        except Exception as exc:
            logger.warning("Capability %s failed: %s", capability, exc)
            return {{"error": str(exc)}}


async def run(goal: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Agent entrypoint for AgentNode agent runner.

    Args:
        goal: The objective for this agent.
        context: Optional context with parameters and configuration.

    Returns:
        Structured result with findings and metadata.
    """
    ctx = context or {{}}
    agent = {_class_name(agent["slug"])}(api_key=ctx.get("api_key"))
    return await agent.execute(goal=goal, context=ctx)
'''


def _class_name(slug: str) -> str:
    return "".join(w.capitalize() for w in slug.replace("-", " ").split())


def _generate_init(agent: dict) -> str:
    return f'"""AgentNode agent package: {agent["name"]}"""\n'


def _generate_pyproject(agent: dict) -> str:
    mod = _module_name(agent["slug"])
    return f'''\
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "{agent["slug"]}"
version = "1.0.0"
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

    print(f"\nDone: {created} agent packs created in starter-packs/")


if __name__ == "__main__":
    main()
