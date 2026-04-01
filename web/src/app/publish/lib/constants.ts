import type { GuidedState, ToolEntry, CapabilityOption } from "./types";

export const MAX_UPLOAD_SIZE_MB = 10;
export const DRAFT_TTL = 45 * 60 * 1000;
export const DRAFT_KEY = "publish_draft";
export const SLUG_PATTERN = /^[a-z0-9-]{3,60}$/;

export const EMPTY_TOOL: ToolEntry = {
  name: "",
  description: "",
  capability_id: "",
  entrypoint: "",
  input_schema: "",
  output_schema: "",
};

export const DEFAULT_GUIDED: GuidedState = {
  name: "",
  package_id: "",
  package_type: "toolpack",
  version: "1.0.0",
  summary: "",
  description: "",
  tools: [{ ...EMPTY_TOOL }],
  frameworks: ["generic"],
  network: "none",
  filesystem: "none",
  code_execution: "none",
  data_access: "input_only",
  user_approval: "never",
  tags: "",
  entrypoint: "",
  use_cases: [],
  examples: [],
  env_requirements: [],
  readme_md: "",
  license: "MIT",
  homepage_url: "",
  docs_url: "",
  source_url: "",
  upgrade_recommended_for: "",
  upgrade_replaces: "",
  upgrade_roles: "",
  upgrade_install_strategy: "local",
};

export const CAPABILITY_FALLBACK: CapabilityOption[] = [
  { id: "pdf_extraction", name: "Extract text & tables from PDFs", category: "Document Processing" },
  { id: "document_parsing", name: "Parse documents (Word, text, HTML)", category: "Document Processing" },
  { id: "document_summary", name: "Summarize long documents", category: "Document Processing" },
  { id: "citation_extraction", name: "Extract citations & references", category: "Document Processing" },
  { id: "web_search", name: "Search the web", category: "Web & Browsing" },
  { id: "webpage_extraction", name: "Extract content from web pages", category: "Web & Browsing" },
  { id: "browser_navigation", name: "Navigate & interact with websites", category: "Web & Browsing" },
  { id: "link_discovery", name: "Discover & validate links", category: "Web & Browsing" },
  { id: "json_processing", name: "Parse, transform & validate JSON", category: "Data Analysis" },
  { id: "csv_analysis", name: "Analyze & transform CSV data", category: "Data Analysis" },
  { id: "spreadsheet_parsing", name: "Parse spreadsheet files", category: "Data Analysis" },
  { id: "data_cleaning", name: "Clean & normalize data", category: "Data Analysis" },
  { id: "statistics_analysis", name: "Run statistical analysis", category: "Data Analysis" },
  { id: "chart_generation", name: "Generate charts & visualizations", category: "Data Analysis" },
  { id: "sql_generation", name: "Generate SQL queries", category: "Data Analysis" },
  { id: "log_analysis", name: "Parse & analyze log files", category: "Data Analysis" },
  { id: "vector_memory", name: "Store & query vector embeddings", category: "Memory & Retrieval" },
  { id: "knowledge_retrieval", name: "Retrieve knowledge from stores", category: "Memory & Retrieval" },
  { id: "semantic_search", name: "Semantic similarity search", category: "Memory & Retrieval" },
  { id: "embedding_generation", name: "Generate text embeddings", category: "Memory & Retrieval" },
  { id: "document_indexing", name: "Index documents for search", category: "Memory & Retrieval" },
  { id: "conversation_memory", name: "Store conversation context", category: "Memory & Retrieval" },
  { id: "email_drafting", name: "Draft emails from prompts", category: "Communication" },
  { id: "email_summary", name: "Summarize email threads", category: "Communication" },
  { id: "meeting_summary", name: "Summarize meetings & calls", category: "Communication" },
  { id: "scheduling", name: "Schedule events & reminders", category: "Productivity" },
  { id: "task_management", name: "Create & manage tasks", category: "Productivity" },
  { id: "translation", name: "Translate between languages", category: "Language" },
  { id: "tone_adjustment", name: "Adjust text tone & style", category: "Language" },
  { id: "code_analysis", name: "Analyze & review code", category: "Development" },
];
