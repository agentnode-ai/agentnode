// Auto-generated from batch verification results
// Last updated: 2026-07-09

export interface ModelResult {
  model: string;
  tier: "S" | "A" | "B" | "C" | "F";
  passed: number;
  total: number;
  s1: boolean;
  s2: boolean;
  s3: boolean;
  s4: boolean;
  testedAt: string;
  // true when this model was not re-tested in the latest batch (result
  // carried over from an earlier run — still listed, honestly marked).
  stale: boolean;
}

export interface ProviderData {
  name: string;
  models: ModelResult[];
}

export const LAST_UPDATED = "2026-07-09";
export const TOTAL_MODELS = 246;
export const S_TIER_COUNT = 222;
export const PROVIDER_COUNT = 40;

export const COMPATIBILITY_DATA: ProviderData[] = [
  {
    name: "ai21",
    models: [
      { model: "jamba-large-1.7", tier: "C", passed: 1, total: 4, s1: true, s2: false, s3: false, s4: false, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "aion-labs",
    models: [
      { model: "aion-2.0", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "aion-3.0", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "aion-3.0-mini", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "amazon",
    models: [
      { model: "nova-2-lite-v1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "nova-lite-v1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "nova-micro-v1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "nova-premier-v1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "nova-pro-v1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "anthropic",
    models: [
      { model: "claude-3-haiku", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "claude-fable-5", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "claude-haiku-4.5", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "claude-opus-4", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "claude-opus-4.1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "claude-opus-4.5", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "claude-opus-4.6", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "claude-opus-4.7", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "claude-opus-4.8", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "claude-opus-4.8-fast", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "claude-sonnet-4", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "claude-sonnet-4.5", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "claude-sonnet-4.6", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "claude-sonnet-5", tier: "A", passed: 3, total: 4, s1: true, s2: false, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "arcee-ai",
    models: [
      { model: "trinity-large-thinking", tier: "A", passed: 3, total: 4, s1: true, s2: false, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "trinity-mini", tier: "C", passed: 1, total: 4, s1: false, s2: true, s3: false, s4: false, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "bytedance-seed",
    models: [
      { model: "seed-1.6", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "seed-1.6-flash", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "seed-2.0-lite", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "seed-2.0-mini", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "cohere",
    models: [
      { model: "command-r-08-2024", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "command-r-plus-08-2024", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "north-mini-code:free", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "deepseek",
    models: [
      { model: "deepseek-chat", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "deepseek-chat-v3-0324", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "deepseek-chat-v3.1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "deepseek-r1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "deepseek-r1-0528", tier: "A", passed: 3, total: 4, s1: true, s2: false, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "deepseek-v3.1-terminus", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "deepseek-v3.2", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "deepseek-v3.2-exp", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "deepseek-v4-flash", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "deepseek-v4-pro", tier: "A", passed: 3, total: 4, s1: true, s2: false, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "google",
    models: [
      { model: "gemini-2.5-flash", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gemini-2.5-flash-lite", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gemini-2.5-flash-lite-preview-09-2025", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gemini-2.5-pro", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gemini-2.5-pro-preview", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gemini-2.5-pro-preview-05-06", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gemini-3-flash-preview", tier: "A", passed: 3, total: 4, s1: true, s2: false, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gemini-3-pro-image", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gemini-3.1-flash-lite", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gemini-3.1-flash-lite-preview", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gemini-3.1-pro-preview", tier: "A", passed: 3, total: 4, s1: true, s2: false, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gemini-3.1-pro-preview-customtools", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gemini-3.5-flash", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gemma-3-12b-it", tier: "F", passed: 0, total: 4, s1: false, s2: false, s3: false, s4: false, testedAt: "2026-07-09", stale: false },
      { model: "gemma-3-27b-it", tier: "C", passed: 1, total: 4, s1: false, s2: true, s3: false, s4: false, testedAt: "2026-07-09", stale: false },
      { model: "gemma-4-26b-a4b-it", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gemma-4-26b-a4b-it:free", tier: "F", passed: 0, total: 4, s1: false, s2: false, s3: false, s4: false, testedAt: "2026-07-09", stale: false },
      { model: "gemma-4-31b-it", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "ibm-granite",
    models: [
      { model: "granite-4.1-8b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "inception",
    models: [
      { model: "mercury-2", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "inclusionai",
    models: [
      { model: "ling-2.6-1t", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "ling-2.6-flash", tier: "A", passed: 3, total: 4, s1: true, s2: true, s3: false, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "ring-2.6-1t", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "kwaipilot",
    models: [
      { model: "kat-coder-pro-v2", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "meta-llama",
    models: [
      { model: "llama-3-8b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "llama-3.1-70b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "llama-3.1-8b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "llama-3.3-70b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "llama-4-maverick", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "llama-4-scout", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "minimax",
    models: [
      { model: "minimax-m1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "minimax-m2", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "minimax-m2.1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "minimax-m2.5", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "minimax-m2.7", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "minimax-m3", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "mistralai",
    models: [
      { model: "codestral-2508", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "devstral-2512", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "ministral-14b-2512", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "ministral-3b-2512", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "ministral-8b-2512", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "mistral-large", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "mistral-large-2407", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "mistral-large-2512", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "mistral-medium-3", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "mistral-medium-3-5", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "mistral-medium-3.1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "mistral-nemo", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "mistral-saba", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "mistral-small-2603", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "mistral-small-3.2-24b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "mixtral-8x22b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "voxtral-small-24b-2507", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "moonshotai",
    models: [
      { model: "kimi-k2", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "kimi-k2-0905", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "kimi-k2-thinking", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "kimi-k2.5", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "kimi-k2.6", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "kimi-k2.7-code", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "nex-agi",
    models: [
      { model: "nex-n2-mini", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "nex-n2-pro", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "nvidia",
    models: [
      { model: "llama-3.3-nemotron-super-49b-v1.5", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "nemotron-3-nano-30b-a3b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "nemotron-3-nano-30b-a3b:free", tier: "A", passed: 3, total: 4, s1: true, s2: false, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "nemotron-3-nano-omni-30b-a3b-reasoning:free", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "nemotron-3-super-120b-a12b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "nemotron-3-super-120b-a12b:free", tier: "A", passed: 3, total: 4, s1: true, s2: false, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "nemotron-3-ultra-550b-a55b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "nemotron-3-ultra-550b-a55b:free", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "nemotron-nano-12b-v2-vl:free", tier: "C", passed: 1, total: 4, s1: true, s2: false, s3: false, s4: false, testedAt: "2026-07-09", stale: false },
      { model: "nemotron-nano-9b-v2:free", tier: "A", passed: 3, total: 4, s1: true, s2: true, s3: true, s4: false, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "openai",
    models: [
      { model: "gpt-3.5-turbo", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-3.5-turbo-0613", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-3.5-turbo-16k", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-4", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-4-turbo", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-4.1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-4.1-mini", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-4.1-nano", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-4o", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-4o-2024-05-13", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-4o-2024-08-06", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-4o-2024-11-20", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-4o-mini", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-4o-mini-2024-07-18", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-5", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-5-codex", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gpt-5-mini", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-5-nano", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-5-pro", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-5.1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-5.1-chat", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-5.1-codex", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gpt-5.1-codex-max", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gpt-5.1-codex-mini", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gpt-5.2", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-5.2-chat", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-5.2-codex", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gpt-5.3-chat", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-5.3-codex", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gpt-5.4", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-5.4-mini", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-5.4-nano", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-5.5", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gpt-audio", tier: "F", passed: 0, total: 4, s1: false, s2: false, s3: false, s4: false, testedAt: "2026-07-09", stale: false },
      { model: "gpt-chat-latest", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gpt-oss-120b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-oss-120b:free", tier: "C", passed: 1, total: 4, s1: true, s2: false, s3: false, s4: false, testedAt: "2026-07-09", stale: false },
      { model: "gpt-oss-20b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "gpt-oss-20b:free", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gpt-oss-safeguard-20b", tier: "B", passed: 2, total: 4, s1: true, s2: false, s3: true, s4: false, testedAt: "2026-04-08", stale: true },
      { model: "o1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "o3", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "o3-mini", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "o3-mini-high", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "o3-pro", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "o4-mini", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "o4-mini-high", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "openrouter",
    models: [
      { model: "auto", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "free", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "poolside",
    models: [
      { model: "laguna-m.1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "laguna-m.1:free", tier: "A", passed: 3, total: 4, s1: true, s2: false, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "laguna-xs-2.1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "laguna-xs-2.1:free", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "laguna-xs.2", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "laguna-xs.2:free", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "qwen",
    models: [
      { model: "qwen-2.5-72b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen-2.5-7b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen-plus", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen-plus-2025-07-28", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen-plus-2025-07-28:thinking", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3-14b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-235b-a22b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-235b-a22b-2507", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-235b-a22b-thinking-2507", tier: "C", passed: 1, total: 4, s1: true, s2: false, s3: false, s4: false, testedAt: "2026-07-09", stale: false },
      { model: "qwen3-30b-a3b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-30b-a3b-instruct-2507", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-30b-a3b-thinking-2507", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3-32b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-8b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-coder", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-coder-30b-a3b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-coder-flash", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-coder-next", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-coder-plus", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-max", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-max-thinking", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3-next-80b-a3b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3-next-80b-a3b-thinking", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3-vl-235b-a22b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3-vl-235b-a22b-thinking", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3-vl-30b-a3b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3-vl-30b-a3b-thinking", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3-vl-32b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3-vl-8b-instruct", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3-vl-8b-thinking", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3.5-122b-a10b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3.5-27b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3.5-35b-a3b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3.5-397b-a17b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3.5-9b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3.5-flash-02-23", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3.5-plus-02-15", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "qwen3.5-plus-20260420", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3.6-27b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3.6-35b-a3b", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3.6-flash", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3.6-max-preview", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3.6-plus", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3.7-max", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "qwen3.7-plus", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "rekaai",
    models: [
      { model: "reka-edge", tier: "A", passed: 3, total: 4, s1: true, s2: false, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "relace",
    models: [
      { model: "relace-search", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "stepfun",
    models: [
      { model: "step-3.5-flash", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "step-3.7-flash", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "tencent",
    models: [
      { model: "hy3", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "hy3-preview", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "hy3:free", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "thedrummer",
    models: [
      { model: "unslopnemo-12b", tier: "F", passed: 0, total: 4, s1: false, s2: false, s3: false, s4: false, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "upstage",
    models: [
      { model: "solar-pro-3", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "x-ai",
    models: [
      { model: "grok-4.20", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "grok-build-0.1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "xiaomi",
    models: [
      { model: "mimo-v2.5", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "mimo-v2.5-pro", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "z-ai",
    models: [
      { model: "glm-4.5", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "glm-4.5-air", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "glm-4.5v", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "glm-4.6", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "glm-4.6v", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "glm-4.7", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "glm-4.7-flash", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "glm-5", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "glm-5-turbo", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
      { model: "glm-5.1", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "glm-5.2", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "glm-5v-turbo", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-04-08", stale: true },
    ],
  },
  {
    name: "~anthropic",
    models: [
      { model: "claude-haiku-latest", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "claude-opus-latest", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "claude-sonnet-latest", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "~google",
    models: [
      { model: "gemini-flash-latest", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "~moonshotai",
    models: [
      { model: "kimi-latest", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "~openai",
    models: [
      { model: "gpt-latest", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
      { model: "gpt-mini-latest", tier: "A", passed: 3, total: 4, s1: true, s2: true, s3: true, s4: false, testedAt: "2026-07-09", stale: false },
    ],
  },
  {
    name: "~x-ai",
    models: [
      { model: "grok-latest", tier: "S", passed: 4, total: 4, s1: true, s2: true, s3: true, s4: true, testedAt: "2026-07-09", stale: false },
    ],
  },
];
