export interface UserInfo {
  id: string;
  username: string;
  publisher?: { slug: string; display_name: string } | null;
}

export interface ToolEntry {
  name: string;
  description: string;
  capability_id: string;
  entrypoint: string;
  input_schema: string;
  output_schema: string;
}

export interface CodeFile {
  path: string;
  content: string;
}

export interface GuidedState {
  name: string;
  package_id: string;
  package_type: "toolpack" | "agent" | "upgrade";
  version: string;
  summary: string;
  description: string;
  tools: ToolEntry[];
  frameworks: string[];
  network: string;
  filesystem: string;
  code_execution: string;
  data_access: string;
  user_approval: string;
  tags: string;
  entrypoint: string;
  // AI Builder enrichment fields (preserved through guided round-trip)
  use_cases: string[];
  examples: { title: string; language: string; code: string }[];
  env_requirements: { name: string; required: boolean; description: string }[];
  readme_md: string;
  // Links & license
  license: string;
  homepage_url: string;
  docs_url: string;
  source_url: string;
  // Agent-specific fields (only used when package_type === "agent")
  agent_entrypoint: string;
  agent_goal: string;
  agent_allowed_packages: string;
  agent_max_iterations: number;
  agent_max_tool_calls: number;
  agent_max_runtime_seconds: number;
  agent_stop_on_final_answer: boolean;
  agent_stop_on_consecutive_errors: number;
  agent_isolation: string;
  agent_persistence: string;
  // Upgrade-specific fields (only used when package_type === "upgrade")
  upgrade_recommended_for: string;
  upgrade_replaces: string;
  upgrade_roles: string;
  upgrade_install_strategy: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface CapabilityOption {
  id: string;
  name: string;
  category: string;
}

export type InputTab = "describe" | "import" | "manifest";

export interface PublishDraft {
  tab: InputTab;
  description?: string;
  importPlatform?: string;
  importCode?: string;
  manifestText?: string;
  guided?: GuidedState;
  source?: string;
  hasBuilderArtifact?: boolean;
  createdAt: number;
  /* Import conversion metadata — preserved across login redirect */
  importConfidence?: { level: string; reasons: string[] };
  importDraftReady?: boolean;
  importWarnings?: string[];
  importGroupedWarnings?: { message: string; category: "blocking" | "review" | "info" }[];
  importChanges?: string[];
}
