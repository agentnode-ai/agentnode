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
}
