// Curated release data for /changelog, distilled from sdk/CHANGELOG.md
// (the engineering source of truth). Dates are verified against PyPI upload
// timestamps ("pypi") or git tag commit dates ("tag") — never invented.
// Maintenance: add one entry per release as part of release prep.

export interface ChangelogRelease {
  version: string;
  /** ISO date (YYYY-MM-DD), verified via PyPI or git tag; null if no verifiable source exists. */
  date: string | null;
  dateSource: "pypi" | "tag" | null;
  title: string;
  /** 1–2 citable sentences. */
  summary: string;
  highlights: string[];
  /** Short note for behavioral/breaking changes. */
  breaking?: string;
  /** Set when this version is published on PyPI. */
  onPyPI: boolean;
  /** Set when a public git tag v<version> exists. */
  hasTag: boolean;
  /** Extra context, e.g. for the unpublished git-only era. */
  notes?: string;
}

const GITHUB_REPO = "https://github.com/agentnode-ai/agentnode";

export function pypiUrl(r: ChangelogRelease): string | null {
  return r.onPyPI ? `https://pypi.org/project/agentnode-sdk/${r.version}/` : null;
}

export function tagUrl(r: ChangelogRelease): string | null {
  return r.hasTag ? `${GITHUB_REPO}/releases/tag/v${r.version}` : null;
}

export function anchorId(r: ChangelogRelease): string {
  return `v${r.version.replace(/\./g, "-")}`;
}

export const RELEASES: ChangelogRelease[] = [
  {
    version: "0.17.0",
    date: "2026-07-03",
    dateSource: "pypi",
    title: "MCP network isolation",
    summary:
      "Community MCP servers no longer have an open default-network path. A community MCP runs only when it is pinned and preinstalled into a sealed volume — with no network by default, or a sealed egress allowlist when it declares allowed domains — and non-preinstalled runtime-fetch servers are refused.",
    highlights: [
      "MCP servers no longer use an open default network path — networking is default-deny",
      "Community MCPs must be pinned, preinstalled, and sealed, or they are refused (floating `npx`/`uvx`/`latest`/git/url runtime fetches are rejected)",
      "Network access is granted only through a declared `mcp_allowed_domains` sealed egress allowlist; otherwise the container runs with no network",
      "Credentialed MCP launches use default-deny networking plus sealed egress allowlists; cleanup and secret hygiene are enforced across launch, timeout, and failure paths",
    ],
    breaking:
      "Community MCP servers that ship only an mcp_command (floating npx/uvx runtime fetch) are now refused. Pin an exact mcp_install so the server is preinstalled into a sealed volume, and declare mcp_allowed_domains for any runtime egress; without declared domains the server runs with no network. Credentialed and curated/trusted host flows are unchanged.",
    onPyPI: true,
    hasTag: true,
  },
  {
    version: "0.16.0",
    date: "2026-06-12",
    dateSource: "pypi",
    title: "Registry providers in auth CLI and setup wizard",
    summary:
      "The auth CLI and setup wizard now surface every LLM provider from the registry — OpenAI, Anthropic, OpenRouter, DeepSeek, Mistral, Qwen, Gemini, and keyless local Ollama — with one source of truth and no hardcoded provider lists.",
    highlights: [
      "`agentnode auth status` lists all registry providers with storage backend and effective source; Ollama shows as a local keyless provider",
      "`agentnode auth test` validates any compatible provider via a free no-completion probe; `auth test ollama` is a reachability check that never starts Ollama",
      "Setup wizard offers all providers in a grouped menu (Recommended / More / Local); selecting Ollama never asks for an API key",
      "Custom endpoints configured under `llm.providers.<name>` appear in status and test automatically",
    ],
    onPyPI: true,
    hasTag: true,
  },
  {
    version: "0.15.0",
    date: "2026-06-11",
    dateSource: "pypi",
    title: "Generic OpenAI-compatible LLM providers",
    summary:
      "The LLM runtime can bind any endpoint that speaks the OpenAI-compatible protocol. Built-in presets cover OpenRouter, DeepSeek, Mistral, Qwen, Gemini, and Ollama — the first key-free, cost-free local agent path.",
    highlights: [
      "Custom endpoints are plain config entries (`llm.providers.<name>` with base_url and model) — no code change per provider",
      "Ollama as an opt-in keyless local provider: no account, no per-token cost, never bound by surprise",
      "Per-provider credentials via environment variable or stored vault key; environment always wins",
      "Sandboxed agents inherit every provider automatically — keys stay host-side, the container only sees RPC completions",
    ],
    onPyPI: true,
    hasTag: true,
  },
  {
    version: "0.14.0",
    date: "2026-06-11",
    dateSource: "pypi",
    title: "Setup wizard: guided credentials + sandbox onboarding",
    summary:
      "`agentnode setup` now walks through optional LLM credentials and local-sandbox readiness — store a key in the OS keychain, diagnose Docker/image state, and optionally pull the pinned sandbox image, all in one guided flow.",
    highlights: [
      "Credential screen with hidden input or env import, honest storage label, and an optional free key test",
      "Sandbox screen shows the doctor's diagnosis and offers the digest-pinned image pull (TTY-only, explicit Yes)",
      "Optional 'enable sandboxed community agents' prompt — only offered when the sandbox is fully ready, default No",
      "No keys in output or config files; pull failures never abort setup",
    ],
    onPyPI: true,
    hasTag: true,
  },
  {
    version: "0.13.0",
    date: "2026-06-11",
    dateSource: "pypi",
    title: "Credential vault for LLM providers",
    summary:
      "API keys now live in the OS keychain (Windows Credential Manager, macOS Keychain, Linux Secret Service) instead of plaintext files, and the LLM runtime finally reads stored credentials — including the sandboxed-agent broker, where keys never enter the container.",
    highlights: [
      "`agentnode auth set <provider>` stores keys in the OS keychain, with an honest plaintext-file (0600) fallback when no keychain exists",
      "`agentnode auth test <provider>` validates the effective key via a free endpoint — no completion call, no cost",
      "`auth status` shows the effective source per provider, including when an environment variable shadows a stored key",
      "Keys are never printed, logged, echoed from provider responses, or sent into the sandbox container",
    ],
    onPyPI: true,
    hasTag: true,
  },
  {
    version: "0.12.1",
    date: "2026-06-10",
    dateSource: "pypi",
    title: "agent_sandbox config fix",
    summary:
      "Fixes the config loader silently stripping a hand-written agent_sandbox section, so the documented `agent_sandbox.enabled` config path and the host LLM ceiling now actually take effect.",
    highlights: [
      "`agentnode config set agent_sandbox.enabled true|false` works and stores a real boolean",
      "The nested `agent_sandbox.llm` host ceiling survives config loading",
    ],
    onPyPI: true,
    hasTag: true,
  },
  {
    version: "0.12.0",
    date: "2026-06-10",
    dateSource: "pypi",
    title: "Sandboxed community agents (flag-gated)",
    summary:
      "Community agents can now run sandbox-or-fail-closed in the pinned container image — never on the host — with a host-side LLM broker, default-deny credential policy, per-run caps, and a sanitized audit record. Default OFF; opt in via AGENTNODE_AGENT_SANDBOX=1.",
    highlights: [
      "Verified/unverified agents run in the sandbox or not at all — no host fallback anywhere on the path",
      "Host-side LLM broker: provider API keys never enter the container (container env is PYTHONPATH only)",
      "`llm_access` manifest block is default-deny, with max_calls / max_input_chars / max_output_chars caps and an optional model allowlist; the host ceiling always wins",
      "Every sandboxed run writes one aggregated, sanitized audit record — never prompts, keys, or raw provider errors",
    ],
    onPyPI: true,
    hasTag: true,
  },
  {
    version: "0.11.4",
    date: "2026-06-09",
    dateSource: "pypi",
    title: "Publish confirm gate",
    summary:
      "`agentnode publish` now asks for confirmation before uploading; non-interactive publishes require an explicit `--yes`.",
    highlights: [
      "Interactive prompt (default No) prevents accidental publishes; `--yes` for CI",
    ],
    breaking:
      "Non-interactive publish without --yes now refuses instead of uploading silently.",
    onPyPI: true,
    hasTag: true,
  },
  {
    version: "0.11.3",
    date: "2026-06-09",
    dateSource: "pypi",
    title: "Test hygiene + multi-tool run guidance",
    summary:
      "Clearer `agentnode run` errors for multi-tool packs (lists available tools and the slug:tool syntax) and a stale lock-integrity test fix.",
    highlights: [
      "Multi-tool packs without an explicit tool name get an actionable error instead of a generic import failure",
    ],
    onPyPI: true,
    hasTag: true,
  },
  {
    version: "0.11.2",
    date: "2026-06-08",
    dateSource: "pypi",
    title: "CLI run resolution fixes",
    summary:
      "`agentnode run <slug>` auto-selects a single-tool pack's tool, and the `slug:tool` form resolves the real package trust level and lockfile entry.",
    highlights: [
      "Single-tool packs run without naming the tool; multi-tool packs are never guessed",
      "`slug:tool` no longer downgrades the package to unverified — the sandbox/trust gate keys on the real slug",
    ],
    onPyPI: true,
    hasTag: true,
  },
  {
    version: "0.11.1",
    date: "2026-06-08",
    dateSource: "pypi",
    title: "Bugfix + hardening",
    summary:
      "Installs now target the running Python interpreter (fixes pipx and unactivated-venv setups), and the agent trust gate is locked by a named regression test.",
    highlights: [
      "`installer.resolve_python()` returns sys.executable first — install and run use the same Python",
      "Agent execution-vector invariant documented and regression-tested",
    ],
    onPyPI: true,
    hasTag: true,
  },
  {
    version: "0.11.0",
    date: "2026-06-08",
    dateSource: "pypi",
    title: "Execution Sandbox (isolated or not at all)",
    summary:
      "The execution plane is now sandboxed: community and unverified code — toolpack builds, toolpack runs, MCP servers — runs inside a hardened, digest-pinned container or not at all. There is no host fallback.",
    highlights: [
      "Hardened container execution: read-only, cap-drop ALL, non-root, network none by default, no host mounts, no secrets",
      "Digest-pinned runner image, acquired explicitly via `agentnode sandbox pull` — never a tag, never auto-pulled",
      "`agentnode sandbox` CLI: pull, doctor (diagnose readiness per package), status",
      "Real network-permission enforcement for toolpack runs: allowlist, unknown = deny",
    ],
    breaking:
      "Behavioral change vs 0.5.x: community code requires a container runtime plus the pinned sandbox image; without them execution is blocked (sandbox_unavailable). Curated/trusted packages run on the host as before.",
    onPyPI: true,
    hasTag: true,
    notes:
      "PyPI jumps 0.5.1 → 0.11.0. The 0.5.2–0.10.1 changes below were tagged in git but not published to PyPI.",
  },
  {
    version: "0.10.1",
    date: "2026-05-22",
    dateSource: "tag",
    title: "AsyncAgentNode registry trust parity",
    summary:
      "The async client verifies registry response signatures on trust-critical endpoints, matching the sync clients.",
    highlights: [
      "Signature verification parity across AgentNodeClient, AgentNode, and AsyncAgentNode",
    ],
    onPyPI: false,
    hasTag: true,
    notes: "Tagged in git; first published to PyPI as part of 0.11.0.",
  },
  {
    version: "0.10.0",
    date: "2026-05-22",
    dateSource: "tag",
    title: "Registry response authenticity",
    summary:
      "Trust-critical registry responses are cryptographically verified against pinned Ed25519 keys — a compromised registry or a MitM with a valid TLS cert can no longer serve an attacker's public key.",
    highlights: [
      "Ed25519 signature verification of registry responses (X-AgentNode-Signature header), exact-byte, no canonicalization",
      "Compile-time pinned trust anchors — no env override, no config file, no network fetch",
      "Differentiated error codes for stripped headers (downgrade), bad signatures (tampering), and unknown/expired keys",
    ],
    onPyPI: false,
    hasTag: true,
    notes: "Tagged in git; first published to PyPI as part of 0.11.0.",
  },
  {
    version: "0.9.0",
    date: "2026-05-22",
    dateSource: "tag",
    title: "Online key verification & publisher identity",
    summary:
      "Completes the publisher trust chain: install-time revocation checks against the registry, `agentnode lock verify --online`, and integrity-protected publisher identity in the lockfile.",
    highlights: [
      "Install blocks packages whose publisher key the registry reports as revoked",
      "`lock verify --online` reports active/revoked/unknown/mismatch per signed package, fail-closed",
      "publisher_slug stored write-once in the lockfile and covered by the integrity hash",
    ],
    onPyPI: false,
    hasTag: true,
    notes: "Tagged in git; first published to PyPI as part of 0.11.0.",
  },
  {
    version: "0.8.0",
    date: "2026-05-22",
    dateSource: "tag",
    title: "Publisher signatures",
    summary:
      "Cryptographic proof of package origin: publishers sign packages with Ed25519 keys at publish time, installs verify before writing the lockfile, and an invalid signature is a hard block with no override.",
    highlights: [
      "Deterministic Ed25519 signatures over the canonical package payload, created automatically by `agentnode publish`",
      "Install verification against the cached public key; invalid signatures block before any lockfile write",
      "Missing signatures warn but never block — publisher adoption is gradual, but never silent",
      "`lock verify` and `inspect` show signature status per package",
    ],
    onPyPI: false,
    hasTag: true,
    notes: "Tagged in git; first published to PyPI as part of 0.11.0.",
  },
  {
    version: "0.7.0",
    date: "2026-05-21",
    dateSource: "tag",
    title: "Lockfile integrity",
    summary:
      "Every security-critical lockfile field is covered by a per-entry SHA-256 hash — post-install tampering is detected before any code executes, warned by default and denied in strict mode.",
    highlights: [
      "`agentnode lock seal` / `lock verify` CLI plus automatic sealing at install time",
      "Runtime integrity check before policy checks; strict mode denies tampered entries",
      "Sensitive-change detection flags runtime swaps, entrypoint changes, endpoint redirects, and permission escalation",
    ],
    onPyPI: false,
    hasTag: true,
    notes: "Tagged in git; first published to PyPI as part of 0.11.0.",
  },
  {
    version: "0.6.2",
    date: "2026-05-20",
    dateSource: "tag",
    title: "Connector/remote runtime hardening",
    summary:
      "Credentials for remote/connector tools are domain-bound and HTTPS-only before any request leaves the process.",
    highlights: [
      "HTTPS-only credential enforcement; empty domain binding is a hard deny",
      "Advisory method/action-type and payload-size checks with safe-metadata audit fields",
    ],
    onPyPI: false,
    hasTag: true,
    notes: "Tagged in git; first published to PyPI as part of 0.11.0.",
  },
  {
    version: "0.6.1",
    date: "2026-05-19",
    dateSource: "tag",
    title: "Runtime audit parity & input guard escalation",
    summary:
      "Unified dispatch-level audit events for all runtimes, and path-traversal/URL-anomaly findings escalate from warning to prompt (interactive) or deny (non-interactive).",
    highlights: [
      "runtime_run and mcp_run audit events with no kwargs or result data",
      "Input guard fail-closed when no human is present to confirm",
    ],
    onPyPI: false,
    hasTag: true,
    notes: "Tagged in git; first published to PyPI as part of 0.11.0.",
  },
  {
    version: "0.6.0",
    date: "2026-05-19",
    dateSource: "tag",
    title: "Guard: pre-execution policy gateway",
    summary:
      "AgentNode Guard classifies every tool invocation by action type (read, write, delete, execute, credential use, network egress, …) and applies configurable allow/prompt/deny policy — before any code runs.",
    highlights: [
      "9 action types with safe defaults, strict mode for production/CI, and per-tool overrides",
      "Composite risk scoring; critical risk is always denied, unoverridable",
      "`agentnode guard` CLI: status, set, unset, check (dry-run), reset",
      "Rate limiting, MCP argument inspection, install-time risk preview, and full audit integration",
    ],
    onPyPI: false,
    hasTag: true,
    notes: "Tagged in git; first published to PyPI as part of 0.11.0.",
  },
  {
    version: "0.5.3",
    date: "2026-05-07",
    dateSource: "tag",
    title: "Configurable risk policies",
    summary:
      "Computed risk flags (like external_write_capable) get user-configurable reactions: allow, log, prompt, or deny — default stays audit-only.",
    highlights: [
      "`risk_policies` config section evaluated between the permission check and execution",
    ],
    onPyPI: false,
    hasTag: true,
    notes: "Tagged in git; first published to PyPI as part of 0.11.0.",
  },
  {
    version: "0.5.2",
    date: null,
    dateSource: null,
    title: "Usage risk profile",
    summary:
      "Per-package usage risk scoring, separate from the verification score: static signals (permissions, trust, credentials) plus runtime signals produce a 0–100 score with semantic risk flags.",
    highlights: [
      "`get_risk_profile(slug)` API and Usage Risk section in `agentnode inspect`",
    ],
    onPyPI: false,
    hasTag: false,
    notes: "Untagged development release; first published to PyPI as part of 0.11.0.",
  },
  {
    version: "0.5.1",
    date: "2026-04-27",
    dateSource: "pypi",
    title: "Security visibility & guardrails",
    summary:
      "Visibility hardening: `agentnode inspect`, input guard warnings, plan-level risk warnings, LLM tool-output marking, and an agent auto-install guard — all informational, nothing blocking.",
    highlights: [
      "`agentnode inspect <slug>` — security-focused report for installed packages",
      "Untrusted tool output is truncated and delimiter-wrapped before reaching the LLM",
    ],
    onPyPI: true,
    hasTag: false,
  },
  {
    version: "0.5.0",
    date: "2026-04-27",
    dateSource: "pypi",
    title: "Intelligence, planner & hardening",
    summary:
      "Multi-step planner with policy-checked piping, a typed capability graph powering gap detection and recommendations, credential/audit/logs CLI, and a broad security hardening pass (trust TTL refresh, atomic writes, file locking).",
    highlights: [
      "`agentnode run \"extract from report.pdf then translate to german\"` — multi-step execution with full policy and audit per step",
      "Capability graph with typed edges; doctor and recommend rewritten on top of it",
      "`agentnode auth`, `agentnode audit`, `agentnode logs`, `--json`/`--explain`/`--dry-run` across the CLI",
      "Trust TTL refresh, atomic config/lockfile/credential writes, cross-platform file locking",
    ],
    onPyPI: true,
    hasTag: true,
  },
  {
    version: "0.4.1",
    date: "2026-04-13",
    dateSource: "pypi",
    title: "Security & correctness",
    summary:
      "run_tool(mode=\"auto\") now always executes via subprocess isolation regardless of trust level, making the documented isolation guarantee true by default — plus async-client URL and install-tracking fixes.",
    highlights: [
      "Subprocess isolation by default; mode=\"direct\" remains an explicit opt-in",
      "500 MB download ceiling with per-chunk enforcement",
    ],
    breaking:
      "Tools relying on shared in-process state must pass mode=\"direct\" explicitly.",
    onPyPI: true,
    hasTag: false,
  },
];

export const LATEST = RELEASES[0];

/** Most recent verified release date — used for the visible "Last updated". */
export const LAST_UPDATED = RELEASES.reduce<string>(
  (latest, r) => (r.date && r.date > latest ? r.date : latest),
  ""
);

export const CHANGELOG_MD_URL = `${GITHUB_REPO}/blob/main/sdk/CHANGELOG.md`;
export const PYPI_PROJECT_URL = "https://pypi.org/project/agentnode-sdk/";
export const GITHUB_TAGS_URL = `${GITHUB_REPO}/tags`;
