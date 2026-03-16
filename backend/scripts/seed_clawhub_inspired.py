"""Seed ~35 new packages inspired by ClawHub's most popular skills."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine
from app.shared.meili import sync_package_to_meilisearch
from sqlalchemy import text

TAXONOMY_ENTRIES = [
    # DevOps & Git
    {"id": "github_integration", "display_name": "GitHub Integration", "description": "Manage GitHub repos, issues, and pull requests", "category": "developer-tools"},
    {"id": "gitlab_integration", "display_name": "GitLab Integration", "description": "Manage GitLab projects and merge requests", "category": "developer-tools"},
    {"id": "docker_management", "display_name": "Docker Management", "description": "Manage Docker containers and images", "category": "developer-tools"},
    {"id": "kubernetes_management", "display_name": "Kubernetes Management", "description": "Manage Kubernetes clusters and pods", "category": "developer-tools"},
    {"id": "cicd_management", "display_name": "CI/CD Management", "description": "Trigger and monitor CI/CD pipelines", "category": "developer-tools"},
    # Browser & Web
    {"id": "browser_automation", "display_name": "Browser Automation", "description": "Automate web browsing and form filling", "category": "web-and-browsing"},
    {"id": "screenshot_capture", "display_name": "Screenshot Capture", "description": "Capture screenshots of web pages", "category": "web-and-browsing"},
    # Google & Microsoft
    {"id": "gmail_integration", "display_name": "Gmail Integration", "description": "Send and read Gmail emails", "category": "communication"},
    {"id": "gcal_integration", "display_name": "Google Calendar Integration", "description": "Manage Google Calendar events", "category": "productivity"},
    {"id": "gdrive_integration", "display_name": "Google Drive Integration", "description": "Upload and download Google Drive files", "category": "integration"},
    {"id": "outlook_integration", "display_name": "Outlook Integration", "description": "Send and read Outlook emails", "category": "communication"},
    {"id": "onedrive_integration", "display_name": "OneDrive Integration", "description": "Manage OneDrive files", "category": "integration"},
    {"id": "teams_integration", "display_name": "Teams Integration", "description": "Post to Microsoft Teams channels", "category": "communication"},
    # Messaging
    {"id": "slack_integration", "display_name": "Slack Integration", "description": "Send messages and manage Slack channels", "category": "communication"},
    {"id": "discord_integration", "display_name": "Discord Integration", "description": "Send messages in Discord channels", "category": "communication"},
    {"id": "telegram_integration", "display_name": "Telegram Integration", "description": "Send and receive Telegram messages", "category": "communication"},
    {"id": "whatsapp_integration", "display_name": "WhatsApp Integration", "description": "Send WhatsApp messages", "category": "communication"},
    # Productivity
    {"id": "notion_integration", "display_name": "Notion Integration", "description": "Manage Notion databases and pages", "category": "productivity"},
    {"id": "notes_management", "display_name": "Notes Management", "description": "Manage markdown notes with linking and search", "category": "productivity"},
    {"id": "project_board", "display_name": "Project Board", "description": "Manage kanban boards and cards", "category": "productivity"},
    {"id": "calendar_management", "display_name": "Calendar Management", "description": "Manage calendar events and scheduling", "category": "productivity"},
    # Documents & Files
    {"id": "file_conversion", "display_name": "File Conversion", "description": "Convert between file formats", "category": "document-processing"},
    {"id": "ocr_reading", "display_name": "OCR Reading", "description": "Extract text from images via OCR", "category": "document-processing"},
    # AI & ML
    {"id": "image_generation", "display_name": "Image Generation", "description": "Generate images from text prompts", "category": "vision"},
    {"id": "text_humanization", "display_name": "Text Humanization", "description": "Rewrite AI text to sound human", "category": "language"},
    {"id": "speech_to_text", "display_name": "Speech to Text", "description": "Transcribe audio to text", "category": "language"},
    {"id": "text_to_speech", "display_name": "Text to Speech", "description": "Convert text to speech audio", "category": "language"},
    # Data & Analytics
    {"id": "news_aggregation", "display_name": "News Aggregation", "description": "Aggregate news from multiple sources", "category": "search"},
    {"id": "arxiv_search", "display_name": "ArXiv Search", "description": "Search academic papers on ArXiv", "category": "search"},
    {"id": "data_visualization", "display_name": "Data Visualization", "description": "Create charts and graphs from data", "category": "data-analysis"},
    {"id": "database_access", "display_name": "Database Access", "description": "Connect to and query databases", "category": "data-processing"},
    # Cloud & Infrastructure
    {"id": "aws_integration", "display_name": "AWS Integration", "description": "Manage AWS cloud resources", "category": "integration"},
    {"id": "cloud_deployment", "display_name": "Cloud Deployment", "description": "Deploy apps to cloud platforms", "category": "developer-tools"},
    # Smart Home
    {"id": "home_automation", "display_name": "Home Automation", "description": "Control smart home devices", "category": "integration"},
    {"id": "smart_lighting", "display_name": "Smart Lighting", "description": "Control smart lights", "category": "integration"},
    # CRM & Marketing
    {"id": "crm_integration", "display_name": "CRM Integration", "description": "Manage CRM contacts and deals", "category": "integration"},
    # Media
    {"id": "youtube_analysis", "display_name": "YouTube Analysis", "description": "Search and analyze YouTube videos", "category": "search"},
    # Dev Utilities
    {"id": "regex_building", "display_name": "Regex Building", "description": "Build and test regular expressions", "category": "developer-tools"},
    # Email
    {"id": "email_automation", "display_name": "Email Automation", "description": "Automate email workflows", "category": "communication"},
]

PACKS = [
    # --- GitHub & DevOps (inspired by GitHub 10.6k, GitLab, Docker, K8s) ---
    {
        "slug": "github-integration-pack",
        "name": "GitHub Integration Pack",
        "summary": "Manage GitHub repos, issues, pull requests, and workflows from your agent.",
        "description": "Full GitHub API integration for managing repositories, creating issues, reviewing PRs, triggering Actions, and browsing code. Built on PyGithub.",
        "capabilities": [{"name": "github_manage", "capability_id": "github_integration", "type": "tool"}],
        "entrypoint": "github_integration_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    {
        "slug": "gitlab-connector-pack",
        "name": "GitLab Connector Pack",
        "summary": "Interact with GitLab merge requests, pipelines, and repositories.",
        "description": "Manage GitLab projects, create merge requests, monitor CI/CD pipelines, and browse repository contents via the GitLab API.",
        "capabilities": [{"name": "gitlab_manage", "capability_id": "gitlab_integration", "type": "tool"}],
        "entrypoint": "gitlab_connector_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    {
        "slug": "docker-manager-pack",
        "name": "Docker Manager Pack",
        "summary": "Manage Docker containers, images, and volumes from your agent.",
        "description": "Start, stop, inspect, and remove Docker containers. Build images, manage volumes, and view logs. Uses the Docker Engine API.",
        "capabilities": [{"name": "docker_manage", "capability_id": "docker_management", "type": "tool"}],
        "entrypoint": "docker_manager_pack.tool",
        "runtime": "python",
        "permissions": {"network": "restricted", "filesystem": "none", "code_execution": "limited_subprocess", "data_access": "input_only", "user_approval": "always"},
    },
    {
        "slug": "kubernetes-manager-pack",
        "name": "Kubernetes Manager Pack",
        "summary": "Manage Kubernetes pods, deployments, and services.",
        "description": "Interact with K8s clusters to list pods, scale deployments, view logs, and manage resources via the official kubernetes-client.",
        "capabilities": [{"name": "k8s_manage", "capability_id": "kubernetes_management", "type": "tool"}],
        "entrypoint": "kubernetes_manager_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "always"},
    },
    {
        "slug": "ci-cd-runner-pack",
        "name": "CI/CD Runner Pack",
        "summary": "Trigger and monitor CI/CD pipelines across platforms.",
        "description": "Unified interface for GitHub Actions, GitLab CI, and Jenkins. Trigger builds, check status, view logs, and retry failed jobs.",
        "capabilities": [{"name": "cicd_run", "capability_id": "cicd_management", "type": "tool"}],
        "entrypoint": "ci_cd_runner_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    # --- Browser & Web (inspired by Agent Browser 11.8k, Tavily 8.1k) ---
    {
        "slug": "browser-automation-pack",
        "name": "Browser Automation Pack",
        "summary": "Automate web browsing, form filling, and data extraction.",
        "description": "Headless browser automation using Playwright. Navigate pages, fill forms, click buttons, take screenshots, and extract structured data from dynamic websites.",
        "capabilities": [{"name": "browser_automate", "capability_id": "browser_automation", "type": "tool"}],
        "entrypoint": "browser_automation_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "temp", "code_execution": "limited_subprocess", "data_access": "input_only", "user_approval": "once"},
    },
    {
        "slug": "web-search-pack",
        "name": "Web Search Pack",
        "summary": "AI-optimized web search with structured, agent-friendly results.",
        "description": "Search the web using Tavily, SerpAPI, or DuckDuckGo and return structured results optimized for AI agent consumption. Supports filtering by domain, date, and content type.",
        "capabilities": [{"name": "web_search", "capability_id": "web_search", "type": "tool"}],
        "entrypoint": "web_search_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "screenshot-capture-pack",
        "name": "Screenshot Capture Pack",
        "summary": "Capture full-page or element-level screenshots of any URL.",
        "description": "Take high-quality screenshots of web pages with configurable viewport, device emulation, and element targeting. Returns PNG or JPEG images.",
        "capabilities": [{"name": "take_screenshot", "capability_id": "screenshot_capture", "type": "tool"}],
        "entrypoint": "screenshot_capture_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "temp", "code_execution": "limited_subprocess", "data_access": "input_only", "user_approval": "never"},
    },
    # --- Google & Microsoft (inspired by Gog 14.3k, Microsoft 365) ---
    {
        "slug": "google-workspace-pack",
        "name": "Google Workspace Pack",
        "summary": "Access Gmail, Google Calendar, Drive, Sheets, and Docs.",
        "description": "Full Google Workspace integration. Send and read emails, manage calendar events, upload/download files from Drive, read/write spreadsheets, and edit documents.",
        "capabilities": [
            {"name": "gmail_access", "capability_id": "gmail_integration", "type": "tool"},
            {"name": "gcal_access", "capability_id": "gcal_integration", "type": "tool"},
            {"name": "gdrive_access", "capability_id": "gdrive_integration", "type": "tool"},
        ],
        "entrypoint": "google_workspace_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "temp", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    {
        "slug": "microsoft-365-pack",
        "name": "Microsoft 365 Pack",
        "summary": "Integrate with Outlook, OneDrive, Teams, and Excel via Microsoft Graph.",
        "description": "Access Microsoft 365 services through the Graph API. Send emails, manage calendar events, upload files to OneDrive, post to Teams channels, and read/write Excel workbooks.",
        "capabilities": [
            {"name": "outlook_access", "capability_id": "outlook_integration", "type": "tool"},
            {"name": "onedrive_access", "capability_id": "onedrive_integration", "type": "tool"},
            {"name": "teams_access", "capability_id": "teams_integration", "type": "tool"},
        ],
        "entrypoint": "microsoft_365_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "temp", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    # --- Messaging (inspired by Slack, Discord, Telegram, WhatsApp) ---
    {
        "slug": "slack-connector-pack",
        "name": "Slack Connector Pack",
        "summary": "Send messages, manage channels, and interact with Slack workspaces.",
        "description": "Post messages, read channel history, manage channels, upload files, and respond to Slack events using the Slack Bolt SDK.",
        "capabilities": [{"name": "slack_interact", "capability_id": "slack_integration", "type": "tool"}],
        "entrypoint": "slack_connector_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    {
        "slug": "discord-connector-pack",
        "name": "Discord Connector Pack",
        "summary": "Send messages, manage servers, and interact with Discord channels.",
        "description": "Post messages, read channel history, manage roles, and respond to Discord events using discord.py.",
        "capabilities": [{"name": "discord_interact", "capability_id": "discord_integration", "type": "tool"}],
        "entrypoint": "discord_connector_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    {
        "slug": "telegram-connector-pack",
        "name": "Telegram Connector Pack",
        "summary": "Send and receive Telegram messages via the Bot API.",
        "description": "Create Telegram bots that send messages, photos, documents, and inline keyboards. Supports webhook and polling modes.",
        "capabilities": [{"name": "telegram_interact", "capability_id": "telegram_integration", "type": "tool"}],
        "entrypoint": "telegram_connector_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    {
        "slug": "whatsapp-connector-pack",
        "name": "WhatsApp Connector Pack",
        "summary": "Send and receive WhatsApp messages via the Business API.",
        "description": "Send text messages, images, and documents through WhatsApp Business API. Supports message templates, quick replies, and read receipts.",
        "capabilities": [{"name": "whatsapp_interact", "capability_id": "whatsapp_integration", "type": "tool"}],
        "entrypoint": "whatsapp_connector_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "always"},
    },
    # --- Productivity (inspired by Notion 5k+, Obsidian 5.7k, Trello, Asana) ---
    {
        "slug": "notion-connector-pack",
        "name": "Notion Connector Pack",
        "summary": "Create, read, and manage Notion databases, pages, and blocks.",
        "description": "Full Notion API integration for managing workspaces. Create and query databases, add pages, update properties, and manage block content.",
        "capabilities": [{"name": "notion_manage", "capability_id": "notion_integration", "type": "tool"}],
        "entrypoint": "notion_connector_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    {
        "slug": "markdown-notes-pack",
        "name": "Markdown Notes Pack",
        "summary": "Manage markdown note vaults with linking, tagging, and search.",
        "description": "Create, edit, search, and organize markdown notes with bidirectional linking, tag management, and full-text search. Compatible with Obsidian vaults.",
        "capabilities": [{"name": "manage_notes", "capability_id": "notes_management", "type": "tool"}],
        "entrypoint": "markdown_notes_pack.tool",
        "runtime": "python",
        "permissions": {"network": "none", "filesystem": "workspace_write", "code_execution": "none", "data_access": "input_only", "user_approval": "once"},
    },
    {
        "slug": "project-board-pack",
        "name": "Project Board Pack",
        "summary": "Manage Trello-style kanban boards, lists, and cards.",
        "description": "Create boards, manage lists and cards, assign members, set due dates, and track project progress using the Trello API.",
        "capabilities": [{"name": "board_manage", "capability_id": "project_board", "type": "tool"}],
        "entrypoint": "project_board_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    {
        "slug": "task-manager-pack",
        "name": "Task Manager Pack",
        "summary": "Create, assign, and track tasks across project management tools.",
        "description": "Unified task management interface for Asana, Linear, and Jira. Create tasks, set priorities, assign team members, and update status.",
        "capabilities": [{"name": "task_manage", "capability_id": "task_management", "type": "tool"}],
        "entrypoint": "task_manager_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    {
        "slug": "calendar-manager-pack",
        "name": "Calendar Manager Pack",
        "summary": "Manage calendar events, scheduling, and availability across providers.",
        "description": "Create, update, and delete calendar events with support for Google Calendar, Outlook, and iCal. Check availability and schedule meetings.",
        "capabilities": [{"name": "calendar_manage", "capability_id": "calendar_management", "type": "tool"}],
        "entrypoint": "calendar_manager_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    # --- Documents & Files (inspired by PDF Extractor, File Converter) ---
    {
        "slug": "pdf-extractor-pack",
        "name": "PDF Extractor Pack",
        "summary": "Extract text, tables, and images from PDF documents.",
        "description": "Parse PDF files to extract structured text, data tables, embedded images, and metadata. Supports OCR for scanned documents via pytesseract.",
        "capabilities": [{"name": "extract_pdf", "capability_id": "pdf_extraction", "type": "tool"}],
        "entrypoint": "pdf_extractor_pack.tool",
        "runtime": "python",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "file-converter-pack",
        "name": "File Converter Pack",
        "summary": "Convert files between formats: PDF, DOCX, HTML, Markdown, CSV, and more.",
        "description": "Bi-directional file format conversion supporting PDF, DOCX, HTML, Markdown, CSV, JSON, XLSX, and plain text. Preserves formatting where possible.",
        "capabilities": [{"name": "convert_file", "capability_id": "file_conversion", "type": "tool"}],
        "entrypoint": "file_converter_pack.tool",
        "runtime": "python",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "ocr-reader-pack",
        "name": "OCR Reader Pack",
        "summary": "Extract text from images and scanned documents using OCR.",
        "description": "Optical character recognition for images, screenshots, and scanned PDFs. Supports 100+ languages via Tesseract and EasyOCR.",
        "capabilities": [{"name": "ocr_read", "capability_id": "ocr_reading", "type": "tool"}],
        "entrypoint": "ocr_reader_pack.tool",
        "runtime": "python",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    # --- AI & ML (inspired by Capability Evolver 35.5k, AI Image Gen, Embeddings) ---
    {
        "slug": "ai-image-generator-pack",
        "name": "AI Image Generator Pack",
        "summary": "Generate images from text prompts using Stable Diffusion and DALL-E.",
        "description": "Create images from natural language descriptions using multiple AI models including Stable Diffusion, DALL-E, and Replicate-hosted models.",
        "capabilities": [{"name": "generate_image", "capability_id": "image_generation", "type": "tool"}],
        "entrypoint": "ai_image_generator_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "once"},
    },
    {
        "slug": "text-humanizer-pack",
        "name": "Text Humanizer Pack",
        "summary": "Rewrite AI-generated text to sound more natural and human.",
        "description": "Transforms robotic AI-generated text into natural, human-sounding prose. Adjusts tone, varies sentence structure, and removes common AI patterns.",
        "capabilities": [{"name": "humanize_text", "capability_id": "text_humanization", "type": "tool"}],
        "entrypoint": "text_humanizer_pack.tool",
        "runtime": "python",
        "permissions": {"network": "none", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "speech-to-text-pack",
        "name": "Speech to Text Pack",
        "summary": "Transcribe audio files and streams to text using Whisper.",
        "description": "Convert audio recordings, podcasts, and meeting recordings to text. Supports 50+ languages with timestamps and speaker diarization.",
        "capabilities": [{"name": "transcribe_audio", "capability_id": "speech_to_text", "type": "tool"}],
        "entrypoint": "speech_to_text_pack.tool",
        "runtime": "python",
        "permissions": {"network": "restricted", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "text-to-speech-pack",
        "name": "Text to Speech Pack",
        "summary": "Convert text to natural-sounding speech audio.",
        "description": "Generate high-quality speech audio from text using models like Coqui TTS, OpenAI TTS, and ElevenLabs. Multiple voices and languages available.",
        "capabilities": [{"name": "synthesize_speech", "capability_id": "text_to_speech", "type": "tool"}],
        "entrypoint": "text_to_speech_pack.tool",
        "runtime": "python",
        "permissions": {"network": "restricted", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    # --- Data & Analytics (inspired by News Aggregator, ArXiv, PubMed) ---
    {
        "slug": "news-aggregator-pack",
        "name": "News Aggregator Pack",
        "summary": "Aggregate and summarize news from RSS feeds and news APIs.",
        "description": "Collect news articles from configurable RSS feeds, NewsAPI, and Google News. Filter by topic, date, and source. Returns structured summaries.",
        "capabilities": [{"name": "aggregate_news", "capability_id": "news_aggregation", "type": "tool"}],
        "entrypoint": "news_aggregator_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "arxiv-search-pack",
        "name": "ArXiv Search Pack",
        "summary": "Search and retrieve academic papers from ArXiv.",
        "description": "Query ArXiv for research papers by topic, author, or keyword. Returns paper metadata, abstracts, and download links. Track new papers in your research area.",
        "capabilities": [{"name": "search_arxiv", "capability_id": "arxiv_search", "type": "tool"}],
        "entrypoint": "arxiv_search_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "data-visualizer-pack",
        "name": "Data Visualizer Pack",
        "summary": "Create charts, graphs, and dashboards from data.",
        "description": "Generate bar charts, line graphs, scatter plots, heatmaps, and interactive dashboards from CSV, JSON, or pandas DataFrames using matplotlib and plotly.",
        "capabilities": [{"name": "visualize_data", "capability_id": "data_visualization", "type": "tool"}],
        "entrypoint": "data_visualizer_pack.tool",
        "runtime": "python",
        "permissions": {"network": "none", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    {
        "slug": "database-connector-pack",
        "name": "Database Connector Pack",
        "summary": "Connect to PostgreSQL, MySQL, SQLite, and MongoDB databases.",
        "description": "Universal database client for querying and managing SQL and NoSQL databases. Execute queries, inspect schemas, export results, and manage migrations.",
        "capabilities": [{"name": "query_database", "capability_id": "database_access", "type": "tool"}],
        "entrypoint": "database_connector_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "always"},
    },
    # --- Cloud & Infrastructure (inspired by AWS, Coolify) ---
    {
        "slug": "aws-toolkit-pack",
        "name": "AWS Toolkit Pack",
        "summary": "Manage AWS resources: S3, EC2, Lambda, and more.",
        "description": "Interact with Amazon Web Services via boto3. Manage S3 buckets, EC2 instances, Lambda functions, DynamoDB tables, and CloudWatch logs.",
        "capabilities": [{"name": "aws_manage", "capability_id": "aws_integration", "type": "tool"}],
        "entrypoint": "aws_toolkit_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "always"},
    },
    {
        "slug": "cloud-deploy-pack",
        "name": "Cloud Deploy Pack",
        "summary": "Deploy applications to Vercel, Railway, Fly.io, and Render.",
        "description": "One-command deployment to popular cloud platforms. Supports static sites, Node.js, Python, and Docker-based deployments with environment variable management.",
        "capabilities": [{"name": "deploy_app", "capability_id": "cloud_deployment", "type": "tool"}],
        "entrypoint": "cloud_deploy_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "workspace_read", "code_execution": "limited_subprocess", "data_access": "connected_accounts", "user_approval": "always"},
    },
    # --- Smart Home & IoT (inspired by Sonos 10.3k, Home Assistant, Philips Hue) ---
    {
        "slug": "home-automation-pack",
        "name": "Home Automation Pack",
        "summary": "Control smart home devices via Home Assistant.",
        "description": "Interact with Home Assistant to control lights, thermostats, locks, cameras, and sensors. Supports automations, scenes, and device status queries.",
        "capabilities": [{"name": "home_control", "capability_id": "home_automation", "type": "tool"}],
        "entrypoint": "home_automation_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    {
        "slug": "smart-lights-pack",
        "name": "Smart Lights Pack",
        "summary": "Control Philips Hue and other smart lighting systems.",
        "description": "Manage smart lights: turn on/off, adjust brightness, change colors, set scenes, and create schedules. Supports Philips Hue, LIFX, and Elgato.",
        "capabilities": [{"name": "control_lights", "capability_id": "smart_lighting", "type": "tool"}],
        "entrypoint": "smart_lights_pack.tool",
        "runtime": "python",
        "permissions": {"network": "restricted", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "once"},
    },
    # --- CRM & Marketing (inspired by HubSpot) ---
    {
        "slug": "crm-connector-pack",
        "name": "CRM Connector Pack",
        "summary": "Manage contacts, deals, and pipelines in HubSpot and Salesforce.",
        "description": "Universal CRM integration for managing contacts, companies, deals, and sales pipelines. Supports HubSpot and Salesforce with unified data models.",
        "capabilities": [{"name": "crm_manage", "capability_id": "crm_integration", "type": "tool"}],
        "entrypoint": "crm_connector_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
    },
    # --- YouTube & Media ---
    {
        "slug": "youtube-analyzer-pack",
        "name": "YouTube Analyzer Pack",
        "summary": "Search, analyze, and transcribe YouTube videos.",
        "description": "Search YouTube, get video metadata, download transcripts/captions, analyze comments, and extract audio. Uses the YouTube Data API and yt-dlp.",
        "capabilities": [{"name": "analyze_youtube", "capability_id": "youtube_analysis", "type": "tool"}],
        "entrypoint": "youtube_analyzer_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "temp", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    # --- Regex & Dev Utilities ---
    {
        "slug": "regex-builder-pack",
        "name": "Regex Builder Pack",
        "summary": "Build, test, and explain regular expressions with AI assistance.",
        "description": "Generate regex patterns from natural language, test patterns against sample text, get explanations of complex regex, and convert between regex flavors.",
        "capabilities": [{"name": "build_regex", "capability_id": "regex_building", "type": "tool"}],
        "entrypoint": "regex_builder_pack.tool",
        "runtime": "python",
        "permissions": {"network": "none", "filesystem": "none", "code_execution": "none", "data_access": "input_only", "user_approval": "never"},
    },
    # --- Email Automation (inspired by Email Automation, AgentMail) ---
    {
        "slug": "email-automation-pack",
        "name": "Email Automation Pack",
        "summary": "Send, receive, and automate email workflows with SMTP/IMAP.",
        "description": "Full email automation: send emails via SMTP, read inbox via IMAP, apply filters, auto-respond, and manage email workflows. Supports Gmail, Outlook, and custom SMTP.",
        "capabilities": [{"name": "automate_email", "capability_id": "email_automation", "type": "tool"}],
        "entrypoint": "email_automation_pack.tool",
        "runtime": "python",
        "permissions": {"network": "unrestricted", "filesystem": "none", "code_execution": "none", "data_access": "connected_accounts", "user_approval": "once"},
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
            for fw in ["langchain", "crewai", "generic"]:
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
                "frameworks": ["langchain", "crewai", "generic"],
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
