# Changelog

## 0.24.0 — The container is the boundary

This release makes the execution boundary real rather than advisory. Third-party code
that is merely *trusted* no longer runs on your host by default, a community agent's own
entrypoint can no longer run on the host at all, and the lockfile that decides what may
run is now integrity-checked at run time. It also repairs a defect that breaks fresh
installs of 0.23.0.

**Read the migration notes below before upgrading** — one shipped default changes in a
way you can feel.

### Changed — please read

- **The shipped default `sandbox.host_trust_policy` is now `curated_only`** (was
  `default`). Only `curated`, AgentNode-owned code runs directly on your host. **Trusted
  third-party toolpacks, MCP servers and agents are sandboxed**, and if no container
  runtime is available they are **refused** — there is never a silent fall back to host
  execution. Your own setting is never overwritten: an on-disk value always wins, and
  `~/.agentnode/config.json` already carries `sandbox.host_trust_policy` if you have
  ever run `agentnode setup` or `agentnode config set` — the whole config is written
  back. You pick up `curated_only` only if you have no config file, or one written
  before that key existed.
- **A community agent's own entrypoint can no longer be executed on the host.** The path
  was removed structurally rather than gated: every attempt raises
  `HostAgentExecutionUnsupported` instead of executing the entrypoint. Community agents
  run in the sandbox with `network=none`, a read-only pack and a clean `HOME`, or they do
  not run.
- **Lockfile integrity is enforced at run time.** Every execution path resolves
  `agentnode.lock` once, fail-closed, and evaluates two stages: the per-entry
  `_integrity` field and a global `structure_digest` over the whole file. In the default
  mode a legacy lockfile without those fields is **allowed with one migration warning**;
  with `AGENTNODE_GUARD_STRICT=1` it is denied.
- **`agentnode.lock` is parsed fail-closed.** A duplicate key, invalid JSON, an
  unreadable file, a non-object model, or a missing/unsupported `lockfile_version` now
  raises `LockfileFormatError` instead of being silently tolerated. The CLI prints a
  single-line `Error: …` and exits non-zero rather than a traceback.
- **Direct Mode passes the same gate.** `run_python`'s fail-soft fallback is gone:
  `load_tool` and Direct Mode go through the integrity gate like every other path, and
  the lockfile path is no longer passed into the child process.
- **Host Python environments are mutated under an inter-process lock**, serialised by
  target-environment identity, and **runtime-initiated installation is disabled** — a
  run never installs into your environment behind your back.
- **Installs are a transaction.** The wheel is built before anything is claimed, the
  target environment is locked, artifacts go through a content-addressed store, failures
  land in a durable quarantine, and recovery is an atomic compare-and-remove of our own
  entry.
- **The host-trust decision is taken once per run** from a single fail-closed snapshot
  and is never recomputed downstream; a sandbox launch whose identity cannot be
  established fails closed at plan-build time.

### Fixed

- **Fresh installs work again.** `agentnode-sdk` declared `mcp>=1.0.0` with no upper
  bound, so a new install resolved mcp 2.x, which removed `mcp.server.fastmcp` — and
  `agentnode_sdk.mcp_server` failed at import. The dependency is now `mcp>=1.0.0,<2`.
  Support for mcp 2.x is a migration and will be its own release.
- **MCP pre-installs no longer run out of disk.** The package managers cached inside the
  sandbox's deliberately small 16 MiB `HOME`, so pre-installing any MCP server with a
  real dependency tree failed with `No space left on device`. Both caches now live in the
  sandbox's own 512 MiB `/tmp`; no mount, limit, or host path changed.
- **`agentnode mcp verify` recognises PyPI `==` pins** the way it already recognised npm
  `@` pins, so an exactly pinned PyPI MCP is no longer reported as unpinned.

### Added

- `agentnode lock verify` also checks the **global structure digest**, not only
  per-entry integrity, and reports the status of each stage separately
  (`verified`, `missing`, `mismatch`, `invalid`, `unsupported`).
- New error types on the public surface: `LockfileFormatError`, `ConfigurationError`,
  and `HostAgentExecutionUnsupported`, all subclasses of `AgentNodeError`.
- Pre-import observability for host execution, with remediation text specific to the
  trust tier that was refused.

### Migration

1. **Decide your host-trust policy.** Check what you are on:

   ```
   agentnode config get sandbox.host_trust_policy
   ```

   If it prints `default`, your config carries the key and nothing changes for you. With
   no config file at all the answer is now `curated_only`. To keep the previous, more
   permissive behaviour:

   ```
   agentnode config set sandbox.host_trust_policy default
   ```

   To adopt the new default explicitly, or to sandbox everything including curated code:

   ```
   agentnode config set sandbox.host_trust_policy curated_only
   agentnode config set sandbox.host_trust_policy none
   ```

2. **Install a container runtime if you use trusted third-party packs.** Under
   `curated_only`, trusted toolpacks and MCP servers need Docker or Podman. Without one
   they are refused rather than run on the host. `agentnode doctor` tells you what is
   missing.

3. **Community agents that relied on host execution will stop.** There is no
   configuration that re-enables running a community agent's entrypoint on the host. Tool
   packs and MCP servers are unaffected by that specific removal.

4. **A valid existing `agentnode.lock` keeps working.** `lockfile_version` is unchanged
   at `0.1`. Entries without `_integrity` and a file without `structure_digest` are
   *warned*, not rejected, in the default mode. Run `agentnode lock verify` to see the
   status, and reinstall or re-seal at your convenience.

5. **A malformed `agentnode.lock` now fails.** If your lockfile has duplicate keys,
   invalid JSON, no `lockfile_version`, or is not a JSON object, it was tolerated before
   and is refused now. `agentnode lock verify` names the problem; repair the file, or
   remove it and reinstall your packages to regenerate it.

6. **If you run with `AGENTNODE_GUARD_STRICT=1`**, note that strict mode now also denies
   a lockfile whose structure digest is missing or mismatched. Re-seal before enabling
   it in CI.

## 0.23.0 — Prove MCP package ownership from the terminal

### Added

- `agentnode mcp ownership challenge --registry npm|pypi <package>` issues a
  one-time challenge: the server returns a token and a keyword
  (`agentnode-ownership-<token>`). Add the keyword to your package's metadata
  and publish a new version — only someone with publish rights can do that. The
  token is shown once and is stored server-side only as a hash; it is never
  written to local config.
- `agentnode mcp ownership verify --registry npm|pypi <package>` checks the
  latest published version for the keyword and, on a match, records strong
  ownership evidence. It reports `verified` / `pending` / expired / no-challenge
  / package-not-found / registry-unavailable clearly, and exits non-zero until
  ownership is verified (CI-gateable).

Verifying ownership does **not** publish anything: MCP listings still remain
review-gated until the sandbox-smoke gate is built. The same flow is available
in the browser on `/mcp/submit`.

## 0.22.0 — MCP from the terminal + refreshed compatibility

### Added

- `agentnode init --type mcp` scaffolds an MCP server listing: a schema-valid
  manifest (runtime `mcp`, a pinned `mcp_server` command with an npm/PyPI
  package and `source_repo`, honest permissions) plus a README that walks
  `agentnode mcp verify` → `agentnode mcp submit`. MCP now has the same
  terminal starting point as toolpacks, skills, and agents.

### Changed

- `agentnode publish` detects an MCP manifest (runtime `mcp` or an `mcp_server`
  block) and routes you to `agentnode mcp verify` / `agentnode mcp submit`
  instead of failing deep in toolpack validation. Non-MCP manifests are
  unaffected.
- Refreshed model compatibility data: 246 scored models (222 S-tier), current
  flagships added, models untestable in the latest batch kept with their prior
  result marked legacy, and only hard-evidence removals delisted.

## 0.21.0 — Community agents run sandboxed by default

### Changed

- **`agent_sandbox.enabled` now defaults to ON.** Community agents
  (`verified`/`unverified`/unknown) run **sandbox-or-refuse**, consistent with
  toolpacks and MCP servers: in an isolated container when a container runtime
  and the pinned image are present, otherwise **refused cleanly** — never a host
  fallback. Set `agent_sandbox.enabled=false` (or `AGENTNODE_AGENT_SANDBOX=0`)
  to restore the previous behavior (community agents refused outright).
- `trusted`/`curated` agents are **unchanged**: they still run on the host under
  the `default` host-trust policy. `sandbox.host_trust_policy` can still tighten
  this (`curated_only`, `none`).
- The setup wizard's sandbox screen now states community agents are isolated by
  default and offers to disable, instead of prompting to opt in.

### Security

- Agent containers are regression-locked to the hardened profile: CPU, memory
  and PID limits, read-only rootfs, all Linux capabilities dropped,
  `no-new-privileges`, a non-root user, `noexec`/`nosuid` `/tmp`, a clean
  ephemeral HOME, and `network=none`. The container env carries only
  `PYTHONPATH` — no host secret reaches the container in the environment or on
  the argv; the LLM key stays host-side behind the broker.
- The in-container agent wrapper installs a fork/exec/subprocess guard before
  loading agent code (defense-in-depth; the container flags are the real
  boundary).
- End-to-end verified on a real container host: a community agent runs isolated
  with tool calls brokered host-side (allowlist enforced), LLM calls through the
  host broker, and no host env/filesystem/network/key leakage — with no host
  fallback when the sandbox is unavailable.

### Fixed

- The gated agent-sandbox end-to-end test now injects the real container backend
  past the test-suite's fake, so it actually exercises the real
  `run_agent → run_agent_sandboxed` path on a Docker host.

## 0.20.0 — Credentialed toolpacks (bring your own API key)

### Added

- Toolpacks can declare the credentials they need (`env_requirements` in the
  manifest: names, required flag, description — never values). The declaration
  and the publisher's `permissions.network.allowed_domains` egress allowlist
  are sealed into the lockfile at install (integrity-covered; tampering with
  either breaks lockfile integrity).
- `agentnode install` lists the declared environment variables with their
  set/not-set status and tells you to set required ones before running.
- Sandboxed community toolpacks can now receive user-provided API keys under
  a fail-closed regime mirroring credentialed MCPs: a consent prompt (or a
  stored grant) bound to the exact package identity
  (slug + version + artifact hash + key names + domains), an enforced egress
  proxy restricted to the sealed `allowed_domains`, and name-only key
  pass-through (`--env NAME` — the value never appears on argv, in the
  process spec, or in logs).

### Changed

- Running a toolpack with a missing *required* environment variable now fails
  before dispatch with an actionable, value-free message
  (`mode_used="credentials_missing"`) instead of a cryptic tool error.

### Security

- A credentialed toolpack without a valid, non-empty `allowed_domains`
  allowlist is refused — a secret never rides an open or unrestricted
  network. Consent cannot transfer between packages, versions, artifacts,
  key sets, or domain sets. There is no fallback run without these
  protections. Non-credentialed toolpacks behave exactly as before.

## 0.19.0 — Full setup wizard coverage

### Added

- Expanded `agentnode setup` to cover the full first-class configuration surface
  with multiple-choice prompts and clearly marked recommended defaults.
- Added a Guard posture step with Balanced, Strict, Permissive and Customize-each
  options.
- Added setup support for `sandbox.host_trust_policy`.
- Added an optional Advanced gate for niche first-class settings.

### Changed

- Promoted minimum trust level to a direct setup screen.
- Added consistent `(recommended)` labels across setup choices.
- A stored LLM key provider can now be offered as the default LLM provider,
  matching the existing Ollama behavior.

### Hardened

- Invalid interactive menu input now re-prompts instead of silently falling back
  to the recommended option — a typo can no longer set a security choice unnoticed.
- Non-interactive (non-TTY) setup continues to take the recommended defaults and
  never hangs.
- Accepting all recommended choices reproduces the current default configuration,
  so upgrading changes nothing until you opt in.

### Notes

- Deeply nested configuration (`llm.providers`, the `agent_sandbox.llm` ceiling,
  and `guard` overrides / rate limits) remains CLI/manual-only by design and is
  surfaced as follow-up commands rather than turned into a wizard editor.
- This release changes only the setup wizard; runtime behavior and the default
  configuration are unchanged.

## 0.18.0 — User-controlled host-trust policy

### Added

- **`sandbox.host_trust_policy`** — a new config key that lets you decide which
  trust tiers may run directly on your host: `default` (curated + trusted on the
  host, today's behavior), `curated_only` (trusted is sandboxed), or `none`
  (everything is sandboxed). Set it with
  `agentnode config set sandbox.host_trust_policy curated_only`. AgentNode trusting
  a package's code is not the same as you trusting it with your machine — this
  setting closes that gap.
- **`agentnode sandbox doctor <slug>` is now host-trust-policy aware** — it explains
  when a package is sandboxed by the policy and distinguishes "reinstall to rebuild
  the sandbox volume" from "the publisher must pin this MCP" from a `none`/system-
  package warning, and flags that a sandboxed agent runs the strict profile.
- New `docs/security/host-trust-policy.md`.

### Changed

- Toolpacks, MCP servers, and agents all honor `sandbox.host_trust_policy` through
  one shared decision. Under a stricter policy the installer builds the sealed
  sandbox volume for trusted/curated packages at install time, and the lockfile
  records `build_mode`, `pinnable`, and `effective_host_trust_policy_at_install`
  (mutable metadata — the runtime still re-verifies the volume itself).

### Hardened

- A tier the active policy sandboxes is **fail-closed**: if it cannot actually be
  isolated (no container runtime, no built volume, a non-pinnable MCP) it is
  refused, never run on the host as a fallback.

### BREAKING / Upgrade Notes

- **Not breaking by default.** The default policy is `default`, which is exactly
  today's behavior — nothing changes until you opt in.
- **Opting into `curated_only`/`none` isolates more strongly and can break
  trusted/curated packages** that expect the host filesystem, broad tools, host LLM
  keys, or network — the sandbox has none of these. **Agents are the strictest
  case:** a sandboxed agent runs the same strict community profile (declared tools
  only, default-deny host-brokered LLM, `network=none`, read-only `/pack`), with no
  special rights for trusted agents. After tightening the policy, **reinstall**
  affected packages so their sealed volume is built (`agentnode install <slug>`);
  run `agentnode sandbox doctor <slug>` to see what each package needs. Community
  agents remain governed by the separate opt-in `agent_sandbox.enabled` flag.

## 0.17.0 — MCP network isolation

### Changed

- Community MCP servers now run only when pinned and preinstalled into a sealed
  volume. Preinstalled community MCPs run with no network by default, or behind a
  sealed egress allowlist when `mcp_allowed_domains` are declared.

### Hardened

- Removed the open-network runtime-fetch path for community MCPs. Non-preinstalled
  or floating community MCPs — including `npx`, `uvx`, `latest`, git, or URL-based
  runtime fetches — are now refused fail-closed. The old `network_level`
  open-network grant path is no longer honored for community MCP runtime execution.

### BREAKING / Upgrade Notes

- Community MCPs that ship only an `mcp_command` are now refused instead of running
  with an open network. To migrate, declare an exact pinned `mcp_install` so the MCP
  is preinstalled into a sealed volume. If runtime network access is required,
  declare `mcp_allowed_domains`; without declared allowed domains, the MCP runs with
  no network.
- The credentialed MCP secret flow is unchanged. Curated/trusted MCP behavior is
  unchanged.

## 0.16.0 — Registry providers in auth CLI and setup wizard

### Added

- **`agentnode auth status` now surfaces all registry providers** — OpenAI,
  Anthropic, OpenRouter, DeepSeek, Mistral, Qwen, Gemini — with their storage
  backend and effective source, plus **Ollama as a local keyless provider**
  (shown as selected/configured/not selected instead of "missing"). Custom
  providers configured under `llm.providers.<name>` appear automatically.
- **`agentnode auth test` supports the registry providers.** Compatible
  providers are validated with a free, no-completion `GET {base_url}/models`
  probe; `agentnode auth test ollama` is a keyless reachability check
  (exit 0 reachable, exit 3 unreachable — never "rejected", and it never
  starts Ollama). Custom configured endpoints can be tested the same way.
- **The setup wizard lists all registry providers**, grouped for readability
  (Recommended / More / Local), with Skip remaining the default. Selecting
  Ollama never asks for an API key — it sets `llm.default_provider` through
  the wizard's normal save step.
- Additive `vendor` field in host-side LLM bindings: logs and tooling can now
  show the real provider name; the protocol discriminator used by the broker
  is unchanged.

### Changed

- The auth CLI and the setup wizard now use the provider registry as their
  single source of truth — no hardcoded provider lists or environment
  variable names remain in those surfaces.
- OpenRouter keeps its dedicated `/auth/key` validation path (its `/models`
  endpoint is unauthenticated and validates nothing).

### Hardened

- Provider response bodies are never printed (they can echo key fragments);
  keys are never printed, logged, or exposed in exceptions.
- Ollama is never started or probed automatically — only an explicit
  `agentnode auth test ollama` performs a localhost reachability check.
- The `vendor` field stays host-side and does not change the broker/sandbox
  wire shape.

### BREAKING / Upgrade Notes

- None. All changes are additive; existing `auth` and `setup` flows behave
  identically for the previously supported providers.

## 0.15.0 — Generic OpenAI-compatible LLM providers

### Added

- **Generic OpenAI-compatible provider registry.** The LLM runtime can now
  bind any endpoint that speaks the OpenAI-compatible protocol. Custom
  endpoints are plain config entries — `llm.providers.<name>` with
  `base_url`, `model`, and optionally `api_key_env`/`requires_key` — no code
  changes needed per provider.
- **Built-in presets** for OpenRouter, DeepSeek, Mistral, Qwen, Gemini
  (Google's OpenAI-compatible endpoint) and Ollama, each with the canonical
  base URL, API-key environment variable, and a sensible default model
  (overridable via `llm.providers.<name>.model`).
- **Ollama as a key-free local provider** — the first agent path with no
  account and no per-token cost. Opt-in only: select it explicitly with
  `agentnode config set llm.default_provider ollama` (or configure it under
  `llm.providers`); AgentNode never probes or binds it by surprise.
- Per-provider credentials: each provider's environment variable (e.g.
  `DEEPSEEK_API_KEY`, `GEMINI_API_KEY`) and stored vault credentials both
  work; the environment always overrides the stored key.

### Changed

- The OpenRouter binding now goes through the shared provider registry
  (same behavior, including the namespaced default model — one code path
  instead of a special case).
- The sandboxed-agent LLM broker automatically inherits every compatible
  provider: keys stay host-side, the container still sees only RPC
  completions, and the C1/C2 access policy applies unchanged.

### Hardened

- No provider keys in logs or error messages (client failures are reported
  by exception type only).
- A custom provider without a configured model is skipped instead of
  guessing — a wrong default model would fail confusingly at call time.
- Keyless providers are never attempted unless explicitly selected or
  configured.

### BREAKING / Upgrade Notes

- None. Existing OpenAI/Anthropic setups (env vars, vault, per-agent config)
  behave identically; the new providers are purely additive.

## 0.14.0 — Setup wizard: guided credentials + sandbox onboarding

### Added

- **`agentnode setup` now guides through optional LLM credentials.** A new
  wizard screen offers storing a provider key (OpenAI, Anthropic, OpenRouter —
  or Skip, the default). Entry is via hidden getpass or an import from an
  existing environment variable; the honest storage label (OS keychain /
  plaintext file) is shown, and an optional key test runs against a free
  endpoint (no completion call) without ever blocking the wizard.
- **`agentnode setup` now includes a "Local sandbox" screen.** It shows the
  doctor's diagnosis (runtime, daemon, pinned image, digest) with clear next
  steps. If only the image is missing, the wizard offers the digest-pinned
  pull — on a TTY and only after an explicit Yes (default No).
- **Optional agent-sandbox enable prompt.** Only when the sandbox is fully
  ready, the wizard asks "Enable sandboxed community agents now? [y/N]"
  (default No). The choice is persisted with the wizard's normal save step;
  cancelling saves nothing.
- The summary and finish screens show credentials, sandbox status, and the
  exact follow-up commands (`agentnode auth status`, `agentnode sandbox
  doctor`, `agentnode sandbox pull`, `agentnode config set
  agent_sandbox.enabled true`).

### Hardened

- No keys in wizard output (masked last-4 only), no keys in config files, no
  keys via CLI arguments — entry is getpass or env import only.
- No automatic Docker pull: the only pull path is the existing, fully guarded
  `agentnode sandbox pull`, offered solely on a TTY after an explicit Yes.
- The sandbox flag is never enabled without an explicit Yes, and never offered
  when the sandbox is not ready.
- Pull failures never abort setup; non-interactive sessions never block
  (credential and sandbox prompts skip themselves with guidance).

### BREAKING / Upgrade Notes

- None. The wizard changes are additive; all defaults preserve previous
  behavior (credentials skipped, sandbox flag stays off). No new dependency.

## 0.13.0 — Credential vault for LLM providers

### Added

- **OS-keychain credential storage:** `agentnode auth set openai|anthropic|openrouter`
  now stores the key in the OS keychain (Windows Credential Manager, macOS
  Keychain, Linux Secret Service). `credentials.json` keeps only non-secret
  metadata for keychain-backed entries. If no keychain is available (e.g.
  headless Linux/CI), storage falls back to the plaintext file (0600) — and
  says so honestly.
- **The LLM runtime now reads stored credentials:** `_auto_detect_llm` falls
  back to the credential store when no env var is set — `agentnode auth set
  openai` is finally picked up by host agents AND the sandboxed-agent LLM
  broker (the key still never enters the sandbox container).
- `agentnode auth test <provider>` — validates the effective key via a free
  provider endpoint (no completion call, no cost). Exit codes: 0 valid,
  1 rejected, 2 not configured, 3 indeterminate (network errors are never
  reported as "invalid").
- `agentnode auth set <provider> --from-env ENV_VAR` — import an existing
  environment key into the store.
- `llm.default_provider` config key — which stored credential to try first
  (`openai`, `anthropic`, `openrouter`). OpenRouter bindings pin the
  namespaced default model (`openai/gpt-4o-mini`).

### Changed

- `agentnode auth status` now leads with an LLM-provider section showing the
  storage backend and the EFFECTIVE source per provider — including an
  explicit "env var X — overrides stored credential" callout when an
  environment variable shadows a stored key.
- `agentnode auth list` shows a storage column (OS keychain / plaintext file).
- Environment variables (and `~/.agentnode/.env`) always override stored
  credentials — explicit/CI intent wins.

### Hardened

- Keys are never printed (masked last-4 only), never logged (provider/backend
  errors are reported by type, never message), never echoed from provider
  responses, and never enter the sandbox container, audit records, manifests,
  or lockfiles. `auth test` never accepts a key as a CLI argument.
- Keychain access is probed once per process with a timeout — a locked or
  hanging Secret Service degrades cleanly to file storage instead of blocking.

### Upgrade Notes

- New dependency: `keyring>=24` (pure Python, installed automatically).
- Honest scope of the OS keychain: it protects against other local users and
  accidental file exposure; it does NOT protect against programs running as
  the same user. The fallback is a plaintext file (0600) when no keychain is
  available — neither mode is "encrypted at rest by AgentNode".
- Existing plaintext credentials keep working unchanged. Nothing migrates on
  read; a credential moves into the keychain the next time you `auth set` it.
- No breaking changes.

## 0.12.1 — agent_sandbox config fix

### Fixed

- **`agent_sandbox` config section was stripped during `load_config()`.** The
  config loader rebuilt the file from defaults and silently dropped a
  hand-written `agent_sandbox` section, so the documented
  `agent_sandbox.enabled: true` config path never took effect (only the
  `AGENTNODE_AGENT_SANDBOX` env var worked), and the host LLM ceiling
  (`agent_sandbox.llm.*`) never reached policy resolution. Both now survive
  loading; the nested `llm` ceiling is passed through verbatim.
- **`agentnode config set agent_sandbox.enabled true|false` now works** (the
  key was missing from the allowed-keys whitelist) and stores a **real
  boolean** — previously a stored string `"false"` would have been truthy,
  i.e. read as enabled.

### Upgrade Notes

- No breaking changes. No behavior change unless you use the `agent_sandbox`
  config path; the env var `AGENTNODE_AGENT_SANDBOX` behaves exactly as
  before. The agent sandbox remains **default OFF**.

## 0.12.0 — Sandboxed community agents (flag-gated)

### Added

- **Agent sandbox (default OFF):** with `AGENTNODE_AGENT_SANDBOX=1` (or config
  `agent_sandbox.enabled: true`), `verified`/`unverified` community agents run
  **sandbox-or-fail-closed** in the pinned container image — never on the host,
  with **no host fallback** anywhere on the path. Tool calls cross an
  allowlisted RPC back to the host's gated runner (the host owns allowlist and
  limits); `trusted`/`curated` agents are unchanged. With the flag OFF
  (default), community agents remain refused exactly as in 0.11.4.
- **Host-side LLM broker:** sandboxed agents request completions via RPC; the
  provider call runs host-side and **provider API keys never enter the
  container** (the container env is only `PYTHONPATH=/pack`).
- **`llm_access` manifest block (default-deny):** a sandboxed agent gets NO
  host LLM unless its manifest declares `llm_access.enabled: true` — analogous
  to `tool_access`. Caps: `max_calls`, `max_input_chars`, `max_output_chars`;
  optional `allowed_models` checks the HOST-chosen model (the agent never picks
  a model; absent = unrestricted, `[]` = refuse-all, manifest+host = both must
  allow). The host ceiling (`agent_sandbox.llm` in `~/.agentnode/config.json`)
  always wins — it can lower caps, restrict models, or disable access entirely.
  Refused/failed LLM calls return **graceful per-call errors** the agent can
  catch; they never crash the run and never fall back to the host.
- **Audit:** every sandboxed run writes ONE aggregated, sanitized record to
  `~/.agentnode/audit.jsonl` (`event: agent_run`, `source: agent_sandbox`) —
  counters, caps, and fixed reason codes only; **never prompts, keys, raw
  provider errors, or agent-authored error text**. Fail-closed refusals
  (missing volume/runtime, session-start failure) are audited too.
- Agent manifest template (`agentnode init`) now documents the opt-in
  `llm_access` block with the caps and `allowed_models`.

### Changed

- `agentnode init` agent template includes the `llm_access` example (newly
  scaffolded packages only; existing manifests are unaffected — an absent
  `llm_access` simply means deny).

### Hardened

- The sandbox path is fail-closed end to end: missing/stale volume, missing
  container runtime or pinned image, sandbox-start failure, or a host-loop
  error all return a clean `sandbox_unavailable`/error result — community
  agent code never executes on the host.
- LLM broker errors are generic and leak-free (no key, no provider internals,
  no prompt echo). A model-allowlist refusal never calls the provider (no
  charge), and the host-side model name is never sent into the sandbox.

### BREAKING / Upgrade Notes

- **None.** With the flag OFF (default), behavior is identical to 0.11.4.
  There are no flag-ON users yet (the flag ships first in this release).
- Enabling the agent sandbox requires a container runtime plus the pinned
  public sandbox image — `agentnode sandbox pull` to fetch it,
  `agentnode sandbox doctor` for diagnosis.
- Operational note for managed hosts (e.g. Coolify): automatic image pruning
  can remove the pinned sandbox image, degrading community execution to
  fail-closed until it is re-pulled. Keep the image pinned (e.g. a minimal
  keep-alive holder container referencing the digest) or re-pull on a
  schedule.

## 0.11.4 — Publish confirm gate

### Added

- **`agentnode publish` now asks for confirmation before publishing.** After the
  preview, the command prompts `Publish <pkg>@<version> to <registry>? [y/N]`
  (default No) and only uploads on explicit `y`. A new `--yes`/`-y` flag skips
  the prompt for CI/automation. `--dry-run` is unchanged (never prompts, never
  publishes). Prevents accidental publishes of the wrong package/version/folder.

### BREAKING / Upgrade Notes

- **Non-interactive publish now requires `--yes`.** Previously `agentnode publish`
  in a non-interactive context (CI, piped stdin, or `AGENTNODE_NON_INTERACTIVE=1`)
  uploaded silently. It now **refuses** without `--yes` and exits non-zero.
  Automation that publishes must add `--yes`. Interactive use is unaffected
  beyond the new prompt.

## 0.11.3 — Test hygiene + multi-tool run guidance

### Fixed

- **Stale lock-integrity field-classification test.** `test_real_lockfile_fields_classified`
  used the V1 `CANONICAL_FIELDS` set, so it flagged the V3 field `publisher_slug`
  as "unclassified" on real lockfiles and failed. The integrity model itself
  already seals `publisher_slug` (`CANONICAL_FIELDS_V3`); only the test was stale.
  Updated to `CANONICAL_FIELDS_V3` — no change to the integrity model.

### Changed

- **Clearer `agentnode run <slug>` error for multi-tool packs.** When a package
  exposes more than one tool and resolution fails without an explicit tool name,
  the error now lists the available tools and points at
  `agentnode run <slug>:<tool>` instead of a generic "no entrypoint" / "Function
  'run' not found". Message-only — single-tool auto-select and multi-tool dispatch
  behaviour are unchanged. Applied consistently to the host and container paths.

## 0.11.2 — CLI run resolution fixes

### Fixed

- **`agentnode run <slug>` auto-selects a single-tool pack's tool.** When a
  toolpack declares exactly one tool with an entrypoint, `run <slug>` (no tool
  name) now resolves that tool instead of a non-existent default `run` function
  (which raised `ImportError: Function 'run' not found`). Multi-tool packs are
  unchanged — the runner does not guess among several tools. Applied
  consistently to the host (`load_tool`) and container (`_resolve_container_target`)
  paths via a shared `_default_tool_entrypoint` helper.
- **`agentnode run <slug>:<tool>` resolves the real package trust/lockfile.**
  The `slug:tool` form is now split at the CLI boundary, so the lockfile entry
  and trust level come from the real slug instead of being treated as an unknown
  (and therefore `unverified`) package. The sandbox/trust gate is unchanged and
  still keys on the real slug — community/unverified packages stay
  sandbox-mandatory or fail-closed. Slug/tool parsing is now a shared
  `references.parse_tool_reference` helper used by both the CLI and the agent
  runtime.

## 0.11.1 — Bugfix + hardening

### Fixed

- **Install targets the running interpreter.** `installer.resolve_python()` now
  returns `sys.executable` first, so the host build (`agentnode install` of a
  trusted/curated pack) and `agentnode run` use the **same** Python. Previously
  it resolved `$VIRTUAL_ENV → ./.venv → PATH python3 → PATH python` and never
  the interpreter actually running AgentNode, so under **pipx** or an
  **unactivated venv** the pack installed into a different environment and the
  run could not import it. The existing fallbacks are kept for the rare case of
  an empty `sys.executable`. Host build path only — the community/sandbox path
  builds with `python -m pip` inside the container and was never affected.

### Hardened

- **Agent execution-vector invariant documented + regression-tested.** An
  agent's own entrypoint code runs on the host (not via `SandboxBackend`), so
  the `trust >= trusted` gate in `run_agent` is a security invariant. Added an
  audit comment at the gate (no logic change) and a named regression test
  asserting that `None`/unknown/`unverified`/`verified`/`preview` agents are
  refused while `trusted`/`curated` pass — locking the gate against silent
  lowering that would run community code unsandboxed.

## 0.11.0 — Execution Sandbox (isolated or not at all)

The execution plane is now sandboxed. Community/unverified code (toolpack
builds, toolpack runs, MCP servers) runs inside a hardened container or **not at
all** — never directly on the host. This closes the exec-sandbox security bow
(P0.0–P0.3) and activates it against a real, digest-pinned image.

### Added

- **Container sandbox** for community execution: install-time builds, toolpack
  runs (built into a per-version volume, run read-only), and MCP servers run in
  a hardened container (`--read-only`, `--cap-drop=ALL`, `--user 1000:1000`,
  `--network none` by default, clean HOME, no host mounts, no secrets).
- **Digest-pinned runner image** — `ghcr.io/agentnode-ai/sandbox@sha256:…`
  (never a tag/`latest`, no auto-pull). Acquired explicitly via
  `agentnode sandbox pull`.
- **`agentnode sandbox` CLI** — `pull` (explicit image pull), `doctor [slug]`
  (diagnose readiness / explain why a package is blocked), `status` (one-line).
  A "Sandbox" line was added to `agentnode doctor`.
- Real network-permission enforcement for toolpack runs (allowlist; unknown =
  deny → `--network none`).

### BREAKING / Upgrade Notes

> This is a behavioral breaking change for users upgrading from 0.5.x. It is
> intentional and security-driven.

- **Community code now runs isolated or fail-closed.** Toolpacks and MCP servers
  from community/unverified publishers require a container runtime (Docker or
  Podman) **and** the pinned sandbox image (`agentnode sandbox pull`). Without
  them, execution is **blocked (`sandbox_unavailable`)** — there is **no host
  fallback**. In 0.5.x this code ran directly on the host; it no longer does.
- **Set up the sandbox** to keep community packages working: install Docker or
  Podman, then run `agentnode sandbox pull`. Run `agentnode sandbox doctor` to
  see exactly what's missing. Curated/trusted packages continue to run on the
  host (no sandbox required).
- **Guard/policy may now prompt or deny** actions that 0.5.x allowed silently
  (pre-execution policy, action-type classification, rate limits, input guard).
- **No lockfile migration needed** — existing 0.5.x `agentnode.lock` files load
  unchanged; entries without integrity/sandbox fields are not blocked.
- CLI is **additive** (no commands removed or renamed).

_(PyPI jumps 0.5.1 → 0.11.0; the 0.6–0.10 changes below were tagged in git but
not previously published.)_

## 0.10.1 — AsyncAgentNode Registry Trust Parity

- **AsyncAgentNode** now verifies registry response signatures on
  trust-critical GET endpoints (parity with sync `AgentNodeClient`
  and `AgentNode`).

## 0.10.0 — Registry Response Authenticity

Cryptographic verification of registry API responses. The SDK no longer
trusts registry metadata based solely on TLS transport — trust-critical
responses are verified against pinned Ed25519 registry keys.

This closes the first-install trust bootstrap gap: a compromised registry
(or MitM with a valid cert) can no longer serve an attacker's public key
to hijack publisher signature verification.

### Added

- **`registry_trust.py`** — new module for registry response authenticity
  verification (TG-4). Ed25519 signature verification against pinned
  registry keys. Exact-byte verification (no canonicalization).
- **`X-AgentNode-Signature` header verification** — format
  `algorithm:key_id:base64_signature`. Validated on trust-critical GET
  endpoints: `/packages/{slug}`, `/packages/{slug}/install-info`,
  `/publishers/{slug}/keys/{key_id}`.
- **`RegistryKey` frozen dataclass** — typed trust anchor with key_id,
  algorithm, public_key, and optional not_after expiry.
- **`REGISTRY_KEYS` immutable mapping** — compile-time trust anchors
  using `MappingProxyType`. Cannot be modified at runtime (no env
  override, no config file, no network fetch).
- **Activation semantics** — ships in bootstrap mode (empty
  `REGISTRY_KEYS`, observational). Once keys are pinned in a release,
  enforcement activates automatically. Missing signature header with
  enforcement active is a hard deny (downgrade protection).
- **Differentiated error codes**:
  - `REGISTRY_SIGNATURE_MISSING` — header stripped (downgrade attack)
  - `REGISTRY_SIGNATURE_INVALID` — known key, bad signature (tampering)
  - `REGISTRY_KEY_UNKNOWN` — unrecognized key_id (SDK outdated)
  - `REGISTRY_KEY_EXPIRED` — key past not_after (SDK outdated)
- **Client integration** — `_verify_registry_signature()` runs in both
  `AgentNodeClient._request()` and `AgentNode._handle()` after HTTP
  status check, before JSON parsing.
- **Key status integration** — `check_key_status()` verifies registry
  response authenticity before trusting key status data.
- **`is_trust_critical(path)`** — explicit regex matching for
  security-critical endpoints. Trailing slash normalized, no URL
  decoding or case folding.

### Security

- DoS protection: header ≤ 8192 bytes, signature == 64 bytes, public
  key == 32 bytes, key_id validated via regex `^[a-z0-9._-]{1,64}$`,
  algorithm allowlist `{"ed25519"}`.
- Base64 decoding uses `validate=True` (rejects non-alphabet characters).
- Registry signatures are transport-bound and ephemeral — not stored in
  the lockfile (publisher signatures remain the artifact trust anchor).
- Anti-replay (response freshness) is NOT in scope for v0.10.0 — deferred
  to TG-5+. TG-4 provides authenticity and integrity only.

## 0.9.0 — Online Key Verification & Publisher Identity

Online publisher key verification, install-time revocation, and offline
publisher identity cache. Together with v0.8.0 Publisher Signatures,
this completes the trust chain: **v0.8.0 verifies who signed** (authenticity),
**v0.9.0 verifies the key is still valid** (freshness) and **caches who
published** (provenance).

### Added

- **`agentnode lock verify --online`** — verify publisher key status
  against the registry. Reports active/revoked/unknown/mismatch per
  signed package. Non-active statuses cause exit code 1.
- **Install-time revocation** — install blocks packages with revoked
  publisher keys when the registry reports key status in the install
  response. No additional network call.
- **`key_status.py`** — new module for online key verification. Uses
  httpx directly (same pattern as trust refresh). Keeps `signature.py`
  offline-only (OC-2 preserved).
- **`OnlineKeyStatus` enum** — typed statuses (active/revoked/unknown/
  mismatch/error) with severity classification (critical/high/medium/
  availability/none).
- **Publisher identity in lockfile** — `publisher_slug` stored at
  Entry-Level as a canonical field (v3). Registry-canonicalized
  (`strip().lower()`) before persistence. Write-once at install time —
  never overwritten by trust refresh or registry sync.
- **canonical_version v3** — integrity hash now covers `publisher_slug`.
  Unsigned packages with `publisher_slug` get v3. v1/v2 entries continue
  to verify against their stored version (backward compatible).
- **Publisher display in CLI** — `agentnode inspect` shows Publisher line.
  `agentnode lock verify` shows `[publisher_slug]` tag per package. Both
  human and JSON output include `publisher_slug`.

### Security

- `lock verify --online` is fail-closed: registry unreachable → exit 1.
- Revocation is asymmetric: new install with revoked key → deny;
  already installed + revoked → warn + audit, no runtime deny.
- `mismatch` (cached ≠ registry public key) is severity "critical" —
  indicates lockfile manipulation, key rebinding, or registry compromise.
- No runtime enforcement — runtime stays offline integrity/authenticity.
- Offline displayed publisher identity is integrity-protected (v3).
- `publisher_slug` is NOT part of the signature payload — it is a registry
  assertion, not a publisher-proven fact. Signature payload stays v1.
- `manifest_to_entry()` does NOT produce `publisher_slug` — security
  boundary between publisher-controlled manifest and registry-asserted
  identity.

## 0.8.0 — Publisher Signatures

Cryptographic proof of package origin. Publishers sign packages with
Ed25519 keys at publish time. Install verifies signatures against the
cached public key before writing the lockfile. Invalid signatures block
install — no override. Missing signatures warn but never block (gradual
publisher adoption).

Together with v0.7.0 Lockfile Integrity, this closes the supply-chain
gap: **v0.7.0 detects post-install mutation** (integrity), **v0.8.0
verifies who authorized the entry** (authenticity).

### Added

- **Ed25519 publisher signatures** — publishers sign a canonical payload
  (slug + version + entrypoint + artifact_hash + tools + permissions +
  all canonical fields) with their Ed25519 private key at publish time.
  Signatures are deterministic: same entry + same key = same signature.
- **Signing key management** — `generate_ed25519_keypair()`,
  `sign_payload()`, `load_signing_key()`, `get_or_create_signing_key()`.
  Private key stored at `~/.agentnode/signing_key` (PEM/PKCS8, 0600
  permissions on POSIX). Permission check warns on too-open files.
- **Publish signing** — `agentnode publish` signs the package
  automatically. Signing failure warns but does not block publishing
  (resilient to key issues). Signature block included in the publish
  API request.
- **Install verification** — `install_package()` verifies the publisher
  signature against the cached public key before `seal_entry()`. Policy:
  - Valid signature: silent (log info)
  - Missing signature: `warnings.warn()` — never silent, never blocks
  - Invalid/malformed/wrong-key signature: `RuntimeError` before
    `update_lockfile()` — install blocked, no partial lockfile write
  - Private key is never loaded during install
- **canonical_version v2** — `_integrity` hash now covers `_signatures`
  when present. Detects signature/public-key swap attacks. v1 entries
  (no `_signatures`) continue to verify against v1 field list. v2 is
  produced automatically when `_signatures` are present.
- **`agentnode lock verify` signature status** — verifies publisher
  signatures alongside integrity for every lockfile entry. Human output
  shows `signature: valid|missing|INVALID|REVOKED|UNKNOWN_KEY` per
  package. JSON output includes `"signatures"` dict per package and
  `"signature_invalid"` list. Exit code 1 on invalid/unknown_key.
  Missing/revoked signatures do not affect exit code.
- **`agentnode inspect` signature status** — shows `Signature` line in
  human output and `"signature"` object in `--json` output with
  `status`, `key_id`, `algorithm`, and `error` fields.
- **`manifest_to_entry()`** — maps publish manifest to lockfile-entry
  format for canonical payload consistency between publish and install.
  Tools normalized to `{name, entrypoint}` only (no `action_type`).
- **`SignatureStatus` enum** — `valid`, `missing`, `invalid`, `revoked`,
  `unknown_key`. Used by all verification paths.
- **`SignatureResult` dataclass** — carries `status`, `slug`, `key_id`,
  and `error` from every signature verification call.
- 68 new tests across 6 test files (signature verification, signing key
  management, publish signing, install verification, lock verify, inspect).

### Security

- **Invalid signature is a hard block.** No `--force`, no override, no
  fallback. This is the one supply-chain barrier that never bends.
- **Signature payload uses v1 canonical fields** — never includes
  `_signatures` (circular: the signature cannot sign itself).
  `_integrity` v2 hash DOES include `_signatures`, protecting against
  signature/public-key swap attacks.
- **Verification uses cached public key only** — no registry call during
  `lock verify` or `inspect`. Offline verification by default.
- **Private key never leaves publisher machine** — never loaded during
  install, lock verify, or inspect. Only used at `agentnode publish`.
- **Lock entry verified against exact lock_entry** — not raw registry
  metadata. Prevents publish-signs-A, install-verifies-B mismatches.
- **Downloaded artifact hash** — lock_entry uses the hash from the
  downloaded artifact, not the registry-provided value. Prevents
  spoofed hash attacks.

### Known Deltas

- **Missing signatures are non-blocking** — by design. Publisher adoption
  is gradual. But missing is never silent: `warnings.warn()` on install,
  visible in `lock verify` and `inspect`.
- **Revocation is status-only** — `SignatureStatus.REVOKED` exists but
  revocation checks require registry calls (Phase 16.6+). Currently no
  key is ever marked revoked.
- **No `--online` flag yet** — `lock verify` and `inspect` use cached
  public key. Online re-fetch and revocation checks deferred.
- **Single signature per entry** — `_signatures.publisher` is an array
  but only the first entry is verified. Multi-signature support deferred.

### Design Constraints

- Signing is Ed25519 only. No algorithm negotiation, no RSA, no ECDSA.
  32-byte keys, 64-byte signatures, no configuration.
- `canonical_version` is explicitly versioned: v1 (14 fields, no
  `_signatures`), v2 (15 fields, includes `_signatures`). Future field
  additions require a new canonical_version.
- Private key format is PEM/PKCS8 (unencrypted). Encryption deferred.
- Key ID format: `ed25519:{sha256_first_16_hex}` — deterministic from
  the public key bytes.

### Migration

- No breaking changes. Existing v0.7.0 lockfiles without `_signatures`
  continue to work. v1 integrity hashes remain valid.
- Run `agentnode lock verify` to see signature status for all entries.
- Run `agentnode inspect <slug>` to see signature details per package.
- Signed packages get `_integrity` v2 automatically on install.
- To generate a signing key: `agentnode publish` creates one on first use
  at `~/.agentnode/signing_key`.

## 0.7.0 — Lockfile Integrity

Detects post-install mutation of lockfile entries. Every security-critical
field (entrypoint, runtime, remote_endpoint, mcp_command, permissions) is
covered by a per-entry SHA-256 hash. Tampered entries are warned on by
default and denied in strict mode — before any code executes.

### Added

- **Per-entry `_integrity` hash** — `seal_entry()` computes a SHA-256
  digest over canonical fields (version, package_type, runtime, entrypoint,
  artifact_hash, tools, permissions, mcp_command, remote_endpoint,
  connector, agent, prompts, resources, assets). Mutable fields
  (trust_level, installed_at, last_trust_check, source, install_path,
  install_mode, capability_ids) are excluded from the hash.
- **`agentnode lock seal`** — computes `_integrity` for all entries
  missing it. `--force` recomputes all entries. Writes atomically.
- **`agentnode lock verify`** — verifies all entries against stored
  hashes. Exit code 1 on mismatch. `--strict` treats missing integrity
  as failure. `--json` for structured output.
- **Install-time sealing** — `install_package()` and `_install_skill()`
  automatically seal new entries. Reinstalls and upgrades recompute the
  hash.
- **Runtime integrity check** — `run_tool()` verifies entry integrity
  before policy checks. Default mode: warn + audit on mismatch. Strict
  mode (`AGENTNODE_GUARD_STRICT=true`): deny before execution.
- **Strict mode deny** — integrity mismatch returns
  `RunToolResult(success=False, mode_used="integrity_denied")` in strict
  mode. Missing `_integrity` never blocks (migration-compatible).
- **Inspect integration** — `agentnode inspect <slug>` shows integrity
  status (verified / missing / MISMATCH) in both human and `--json`
  output.
- **Sensitive change detection** — `detect_sensitive_changes()` compares
  two entries and flags security-relevant mutations: runtime swap,
  entrypoint change, remote endpoint redirect, MCP command change,
  permission escalation.
- **Audit events** — `lock_integrity_check` (runtime mismatch/missing)
  and `lock_seal` (CLI seal operations) added to audit trail.
- 111 new tests across 4 test files.

### Security

- Integrity check runs before policy checks — tampered entries are caught
  before Guard, check_run(), or runtime dispatch sees them.
- No auto-seal on read. Reading a tampered lockfile never legitimizes the
  tampering.
- Audit entries contain only safe metadata (integrity_status,
  canonical_version). No entry content, hashes, or field values.
- `_integrity` is per-entry, not global. Individual entry tampering is
  detected without requiring a full lockfile rehash.

### Known Deltas

- **`trust_level` is mutable** — TTL refresh legitimately updates it.
  Local manipulation of `trust_level` (e.g. `unverified` → `trusted`)
  is not detected by integrity checks. Trust enforcement relies on
  policy/TTL mechanisms.
- **`install_mode` is mutable** — currently UX metadata only. If it
  gains runtime semantics, it must be promoted to canonical.

### Design Constraints

- Signatures are explicitly out of scope (Phase 16+).
- No global lockfile hash — protects per-entry, not entry addition.
- Missing `_integrity` is migration-compatible: no block, no prompt.
- Canonical field list is versioned (`canonical_version: 1`) for future
  evolution without breaking existing hashes.

### Migration

- No breaking changes. Existing lockfiles without `_integrity` continue
  to work without warnings.
- Run `agentnode lock seal` to add integrity hashes to existing entries.
- Run `agentnode lock verify` in CI to detect lockfile drift.
- Enable strict mode (`AGENTNODE_GUARD_STRICT=true`) to deny tampered
  entries at runtime.

## 0.6.2 — Connector/Remote Runtime Hardening

Closes the enforcement gap between Guard and the HTTP boundary for
remote/connector tools. Credentials are now domain-bound and
HTTPS-only before any request leaves the process.

### Added

- **HTTPS-only credential enforcement** — `CredentialHandle` refuses to
  send credentials over non-HTTPS URLs. `_require_secure_target()` runs
  before every `authorized_request()` and `authorized_request_headers()`
  call. Covers `http://`, empty-scheme, and relative URLs.
- **Empty-domain binding denial** — `CredentialHandle` with empty
  `allowed_domains` now raises `PermissionError` instead of allowing any
  host. Closes the open-proxy gap (GAP-1).
- **Method/action-type consistency warnings** — remote runner detects
  mismatches between HTTP method and declared `action_type` (e.g.
  `action_type=read` with `POST`). Advisory only — logged and audited,
  never blocks. Guard remains the policy authority.
- **Request/response size warnings** — remote runner measures JSON
  request payload and response body size. Warns on requests >10 MB or
  responses >50 MB. Never blocks. Audit includes size fields only when
  thresholds are exceeded.
- **Scope/method mismatch logging** — remote runner detects mutating
  HTTP methods (POST/PUT/PATCH/DELETE) when all declared connector
  scopes appear read-only. Heuristic-based, advisory only.
- **Remote audit fields** — `_audit_remote_call()` now records
  `remote_method`, `remote_domain`, `remote_status_code`,
  `remote_duration_ms`, `remote_provider`, and conditional warning
  fields. All fields use `remote_` prefix. No URLs, paths, kwargs,
  bodies, or secrets in audit entries.
- **Guard config cache invalidation** — guard config is reloaded when
  the config file's mtime or size changes, without restarting the
  process.
- 105 new tests across all phases (characterization, enforcement, audit).

### Fixed

- **Word-counter E2E argument shape** — test helper passed arguments in
  wrong format.

### Security

- Deny happens before `httpx.request()` — credentials never reach the
  wire for denied requests.
- Empty `allowed_domains` is a hard deny, not a permissive default.
- Remote runner advisory checks never override Guard decisions.
- Audit entries contain only safe metadata (hostname, status code,
  duration, method). No full URLs, request bodies, or credentials.

## 0.6.1 — Runtime Audit Parity & Input Guard Escalation

Audit completeness and input validation hardening after the v0.6.0
architecture review.

### Added

- **`runtime_run` audit event** — unified dispatch-level audit for all
  runtimes (python, mcp, remote, agent). Emitted after every tool
  execution with runtime type, success/failure, and error summary.
  No kwargs or result data in audit entries.
- **`mcp_run` audit event** — execution-result audit for MCP tool calls,
  analogous to `remote_run`. Records duration, success/failure, and
  error class. Emitted only for actual tool execution, not pre-execution
  guard decisions.
- **Input guard escalation** — `path_traversal` and `url_anomaly`
  findings promoted from warning to blocking:
  - Interactive mode: returns `policy_prompt` (requires confirmation)
  - Non-interactive mode: returns `policy_denied` (fail-closed)
  - Warning-level findings (oversized inputs) remain non-blocking
  - Guard/policy decisions take precedence — input guard never overrides
    an earlier deny
- **`InputFinding` dataclass** — structured findings with `level`
  (warning/prompt), `message`, and `code` fields. `str()` returns the
  message for backward compatibility with `policy_info["input_warnings"]`.
- 50 new tests covering all three sub-phases.

### Fixed

- **Missing `import os` in CLI commands** — `agentnode run --explain`
  and `--json` crashed with `NameError`. Pre-existing since Phase 5.
- **`UnboundLocalError` in MCP runner** — `name` variable was assigned
  inside try block but referenced in except handler. Fixed by
  initializing before the try.

### Security

- Input guard fail-closed in non-interactive mode: if a finding is
  severe enough to require human confirmation, it must not silently
  pass when no human is present.
- Audit entries never contain tool arguments, result data, or secrets.

## 0.6.0 — Guard: Pre-Execution Policy Gateway

Runtime guardrails for AI agent tool calls. Guard sits between the policy
check and tool execution, classifying every tool invocation by action type
and applying configurable policy — before any code runs.

### Added

- **AgentNode Guard** — pre-execution policy gateway with 9 action types:
  `read`, `compute`, `write_local`, `write_external`, `delete`, `execute`,
  `credential_use`, `network_egress`, `unknown`. Each action type maps to
  a policy decision: `allow`, `prompt`, or `deny`.
- **Default policy** — safe defaults out of the box: read/compute/write_local/
  network_egress are allowed; delete/write_external/execute/credential_use
  require confirmation; unknown requires confirmation.
- **Strict mode** — `AGENTNODE_GUARD_STRICT=true` escalates: delete/
  write_external/execute/unknown become hard `deny`, write_local becomes
  `prompt`. Designed for production and CI.
- **Per-tool policy overrides** — `agentnode guard set <action> <decision>
  --tool <slug/tool>` overrides global policy for a specific tool. Overrides
  never bypass critical risk, strict mode, or install/run denial.
- **Guard CLI** —
  - `agentnode guard status` — show resolved policy, strict mode, rate limits
  - `agentnode guard set <action> <decision>` — set global or per-tool policy
  - `agentnode guard unset <action>` — reset to default
  - `agentnode guard check <slug/tool> [--action <type>] [--json]` — dry-run
    policy check without executing
  - `agentnode guard reset` — reset all policies to defaults
- **Action classification** — three-tier: manifest declaration (highest),
  name heuristic (fallback), permission signals (escalation). Permission
  signals add `network_egress`, `execute`, or `credential_use` on top of
  the declared/inferred type.
- **Risk scoring** — composite score from action types, trust level, and
  environment secrets. Levels: low (0–20), medium (21–45), high (46–70),
  critical (71+). Critical risk is always denied (unoverridable).
- **Connector credential bypass** — connectors with declared `auth_type`
  get `credential_use: allow` without prompting when the global policy
  allows it.
- **Agent pre-approved actions** — agent packages declare
  `pre_approved_actions` in their manifest. Pre-approved action types skip
  the prompt. High-risk actions not in the list are denied in
  non-interactive mode, prompted in interactive mode.
- **MCP argument inspection** — deep inspection of tool arguments for path
  traversal, absolute path escape, URL anomalies, shell tokens, oversized
  payloads, excessive nesting, and excessive keys. Schema-aware: suppresses
  false positives when `input_schema` declares free-text or URL fields.
- **Rate limiting** — per-slug sliding window with burst/minute/hour limits.
  Defaults: 60/min, 1000/hour, burst 10. Agents get 120/min, 2000/hour,
  burst 20. Strict mode halves the defaults.
- **Install-time risk preview** — `agentnode install` shows a guard risk
  preview (action types, risk level, policy decisions) before installing.
- **Guard chain tracing** — each policy step recorded as
  `guard_action:{decision}({action_type}[:context])`. Chain visible in
  `guard check --json` and audit entries.
- **CLI confirmation UX** — interactive confirmation prompt with risk
  coloring, mitigation hints, and fail-closed default (No = tool does not
  run).
- **Audit integration** — guard decisions logged to `audit.jsonl` with
  `guard_check` event type. `agentnode audit --type guard_check` filters
  guard events.
- **Skill system** —
  - `agentnode skill install <slug>` — install skill packages
  - `agentnode skill list` — list installed skills
  - `agentnode skill show <slug> [--raw] [--render <args>]` — display skill
    prompts with placeholder rendering
  - MCP server exposing skill prompts and assets
  - Skills bypass guard (no tool execution)
- **`agentnode publish`** — publish packages to the AgentNode registry with
  artifact upload, manifest validation, and pre-publish confirmation gate.
- **Token connector auth** — `auth_type: "token"` supported in connector
  manifests alongside OAuth2.

### Changed

- **Audit display** — `agentnode audit` supports `--type` filter and
  improved formatting for guard events.
- **JSON response guard** — both sync and async SDK clients now validate
  that success-path JSON responses are dicts. Arrays, strings, numbers,
  and null trigger `AgentNodeError` instead of silent pass-through.

### Security

- **OC-1**: Guard imports no runtime-specific modules (python_runner,
  mcp_runner, etc.).
- **OC-2**: Decision path is pure in-memory — no file I/O, no network.
- **OC-3**: Internal exceptions always fail closed — never allow on error.
- **Critical risk is unoverridable** — unverified packages with high-risk
  actions in environments with secrets are always denied, regardless of
  policy configuration.
- **Strict mode tool override bypass prevented** — per-tool overrides are
  ignored in strict mode.
- **Publish confirmation gate** — `agentnode publish` requires explicit
  `y` confirmation (or `--yes`) before uploading.
- **Production startup guard** — backend blocks startup when
  `ENVIRONMENT=production` and default secrets are still configured.

### Fixed

- **Mitigation hint accuracy** — guard prompt hints now reference the actual
  blocking action type instead of the first alphabetical action type.

### Design Constraints

- Guard is a decision layer, not a sandbox. It classifies and gates; it does
  not isolate execution.
- Policy resolution is deterministic: tool_override > global action_policy >
  default. Strict mode replaces the effective policy layer.
- All guard state is in-memory. No guard decision depends on file I/O or
  network calls.
- Agent `pre_approved_actions` come from the manifest and config overrides.
  They are not inherited or guessed.

### Migration

- No breaking changes. All new features are additive.
- Default guard policy matches pre-0.6.0 behavior: read/compute/write_local/
  network_egress allowed, everything else prompted.
- To enable strict mode: `export AGENTNODE_GUARD_STRICT=true`
- To customize policy: `agentnode guard set <action_type> <decision>`
- To add per-tool overrides: `agentnode guard set <action_type> <decision>
  --tool <slug/tool_name>`

## 0.5.3 — Configurable Risk Policies

User-configurable policies for computed risk flags. Extends the risk
profile from Phase 9 with actionable reactions — without changing the
default behavior.

### Added

- **`risk_policies` config section** — per-flag policy configuration
  using the same `allow | log | prompt | deny` values as permissions.
  Default: `external_write_capable: log` (audit only, no blocking).
- **`check_risk_policies()`** — internal policy check that evaluates
  risk flags after `check_run()`. Only fires when the hard policy
  already allowed execution. Hard policy always has priority.
- **Runner integration** — `run_tool()` now evaluates risk policies
  between the permission check and execution. Prompt/deny messages
  clearly identify the risk policy as the source.

### Usage

```bash
# View current setting
agentnode config get risk_policies.external_write_capable

# Require confirmation for external-write-capable packages
agentnode config set risk_policies.external_write_capable prompt

# Block external-write-capable packages
agentnode config set risk_policies.external_write_capable deny

# Reset to audit-only (default)
agentnode config set risk_policies.external_write_capable log
```

### Design

- Default is `log` — nothing is blocked out of the box.
- Risk policies react to **computed** risk flags (from `risk_profile.py`),
  not declared permissions. `permissions.*` handles declared permissions.
- Risk policies only apply to `run_tool()`, not install.
- When a risk policy blocks, the error message says so:
  `"Blocked by risk policy: external_write_capable is configured as deny."`

## 0.5.2 — Usage Risk Profile

Per-package usage risk scoring — separate from the verification score.
Risk answers "how risky is the usage?" not "does it work reliably?"

### Added

- **Usage Risk Profile** — `compute_risk_profile()` scores packages from
  static signals (permissions, trust, credentials) and runtime signals
  (audit deny rate). Score 0–100, level low/medium/high.
- **`get_risk_profile(slug)`** — public API to retrieve the risk profile
  for any installed package. Returns `None` if not installed.
- **Risk flags** — semantic boolean flags like `external_write_capable`
  that categorize risk without affecting the numeric score. Derived from
  network permissions, connectors, and capability IDs.
- **Inspect integration** — `agentnode inspect` now shows Usage Risk
  section (level, score, signals, flags) in both CLI and `--json` output.
- **Backend hint** — optional `risk_score`/`risk_profile` from backend
  metadata is displayed separately but never included in the local score.
- Exports: `RiskProfile`, `compute_risk_profile`, `get_risk_profile`
  available from `agentnode_sdk`.

## 0.5.1 — Security Visibility & Guardrails

Hardening release. Adds visibility into tool inputs, plan-level data flows,
LLM-facing tool outputs, and agent auto-install behavior. All new checks are
informational warnings — no blocking rules that could break existing packs.

### Added

- **`agentnode inspect <slug>`** — security-focused report for installed
  packages: trust level, permissions, runtime, tools, connector info, and
  audit history summary. Supports `--json`.
- **Input guard** — `validate_tool_input()` warns on path traversal patterns,
  oversized strings (>1 MB), oversized collections (>10k items), and URL
  arguments when the package declares `network_level=none`. Warnings appear
  in `RunToolResult.policy["input_warnings"]`.
- **Plan-level risk warnings** — `check_plan_risk()` flags risky step
  combinations: filesystem-read followed by network access, code execution
  followed by network access, and >2 network steps. Warnings shown in CLI
  before execution. `audit_plan()` logs the full plan as a single audit entry.
- **LLM tool output marking** — `mark_untrusted_tool_output()` truncates tool
  results >50 KB before passing to the LLM and wraps content containing
  prompt injection markers in structured delimiters. Injection detection
  triggers a run log event.
- **Agent auto-install guard** — `AgentContext._ensure_installed()` now
  respects `auto_upgrade_policy` from config. When set to `off`, agent
  auto-install is blocked and logged.
- **Shared audit reader** — `read_audit_entries()` extracted to `cli/audit.py`
  as the single entry point for reading `audit.jsonl`. Both `cmd_audit()` and
  `cmd_inspect()` use it.

### Security

- All new checks are warning-only. No existing packs or workflows are blocked.
- Input guard warnings are logged and included in `RunToolResult.policy`.
- Plan risk warnings are informational — shown but never block execution.
- LLM output marking does not claim to prevent prompt injection; it marks
  untrusted data and detects common injection patterns.
- Agent auto-install guard is a policy gate, not a security boundary — it
  respects the user's existing `auto_upgrade_policy` setting.

## 0.5.0 — Intelligence, Planner & Hardening

### Breaking changes

- None.

### Added

- **Multi-step planner** — `agentnode run "extract from report.pdf then
  translate to german"` decomposes tasks via connectors (`then`,
  `and then`, `→`, `after that`, `afterwards`), pipes output between
  steps — so users no longer need manual copy-paste between commands —
  and executes each step via `run_tool()` with full policy/audit.
  Max 3 steps. Available as CLI and Python API (`plan_task()`,
  `plan_and_run()`).
- **Capability graph** — typed weighted edges (`complements`, `requires`,
  `enhances`) between 27 capabilities. Powers gap detection, recommendations,
  and re-ranking. `requires` is used sparingly (only `vector_memory →
  embedding_generation`).
- **Capability taxonomy** — separates runtime capabilities (`active`, have
  installable packages) from authoring capabilities (`planned`, no packages
  yet). `missing_for()` never suggests planned capabilities. Helpers:
  `is_runtime_capability()`, `is_known_capability()`, `list_capabilities()`.
- **`agentnode auth`** — credential management CLI (`set`, `list`, `remove`,
  `status`). Credentials stored with 0600 permissions via atomic writes.
- **`agentnode audit`** — shows recent policy decisions from the append-only
  `audit.jsonl` trail. Supports `--json` for structured output.
- **`agentnode logs`** — shows agent run logs. Supports per-run detail view
  and `--json` output.
- **`agentnode config list`** — shows all settings with descriptions and
  allowed values.
- **`--json` output** on `run`, `resolve`, `doctor`, `recommend`, `audit`,
  `logs` commands.
- **`--explain` on `run`** — shows capability detection, confidence, matched
  package, alternatives, and policy decision for both slug and smart runs.
- **`--dry-run` on `run`** — shows the execution plan (single or multi-step)
  without executing.
- **Synonym matching** — 40+ natural language synonyms for capabilities
  (e.g. "take screenshot" → `screenshot_capture`). Longest phrase matches
  first to prevent partial match ambiguity.
- **Client-side re-ranking** — `rerank()` boosts packages that complement
  installed capabilities, penalizes overlap and already-installed packages.
  Boost clamped to +10/−20 to not oversteer server scores.
- **`RunToolResult.to_dict()`** — structured serialization with policy info
  for `--json` output.
- **`RunToolResult.policy`** — every execution result now carries the policy
  decision (`action`, `reason`, `source`).
- **Run log events** — `step_start`, `step_result`, `llm_call` events for
  agent observability. Retention with configurable `max_age_days` and
  `max_count`.

### Changed

- **`agentnode doctor`** — now uses capability graph for prioritized gap
  detection with accumulated scores and human-readable reasons. Supports
  `--json`.
- **`agentnode recommend`** — rewritten with priority levels (`high`,
  `suggested`, `low`), reason strings, and `--json` structured output.
- **`agentnode resolve`** — re-ranks results using local context (installed
  capabilities and packages). Supports `--json`.
- **Config cleanup** — removed deprecated `allow_unverified` setting. Config
  values validated against allowed sets.
- **Smart run** respects `auto_upgrade_policy` and `install_confirmation`
  from user config. `doctor --fix` respects the same policies.
- **Backward-compatible complements** — `CAPABILITY_COMPLEMENTS` dict now
  derived from the capability graph, not maintained separately.
- **Multi-step CLI guardrails** — `install_confirmation: prompt` respected
  before auto-installing in multi-step mode. Low-confidence steps require
  interactive confirmation or abort in non-interactive mode.

### Security

- **Trust TTL refresh** — `run_tool()` re-checks trust level from backend
  every 7 days. Network failure falls back to cached trust (fail-open on
  read, never on write).
- **`load_tool()` RuntimeWarning** — warns that `load_tool()` bypasses
  policy checks, directing callers to `run_tool()`.
- **Non-interactive mode** — `AGENTNODE_NON_INTERACTIVE=true` disables
  interactive prompts. Policy decisions that require approval are denied
  instead of blocking.
- **Atomic writes** — config, lockfile, and credential store use
  `tempfile` + `os.replace()` to prevent corruption on crash.
- **File locking** — lockfile updates use cross-platform advisory locks
  (`fcntl` on Unix, `msvcrt` on Windows) with sidecar `.lk` files.
- **TOCTOU fix in `remove`** — confirmation prompt runs outside the file
  lock, then re-reads inside the lock before modifying.
- **Credential store** — uses `atomic_write_json()` with `mode=0o600`.
- **Safe piping** — multi-step planner extracts specific keys (`text`,
  `content`, `result`) from previous step output instead of blind `**kwargs`.
- **Install policy in planner** — auto-install uses the standard
  `client.install()` route, respecting `auto_upgrade_policy`,
  `minimum_trust_level`, and `install_confirmation`.

### Fixed

- **Lockfile deduplication** — duplicate package entries no longer
  accumulate across installs.
- **Dead code removal** — removed unused `_policy_check_install` mock from
  conftest, stale imports, unreachable code paths.
- **v0.2 `load_tool` fallback** — no longer attempts entrypoint fallback
  when a tools list is present, preventing false import errors.
- **`_cmd_run_smart` install flow** — no longer silently skips install when
  `auto_upgrade_policy: off`. Shows clear message with manual install
  command.

### Known limitations

- **Planner: max 3 steps** — hard MVP limit, no workaround.
- **Planner: literal connectors only** — splits on "then", "and then", "→",
  "after that", "afterwards". No comma, semicolon, or LLM-based
  decomposition.
- **Piping is heuristic** — extracts `text`/`content`/`result` keys from
  dict outputs. Tools with non-standard output keys get the whole dict
  wrapped as `{"input": dict}`.
- **`_has_explicit_input` knows limited modifiers** — only `target_language`
  is recognized as a modifier key. Additional modifiers must be added
  manually.
- **Taxonomy `active` status is manual** — maintained in
  `capability_taxonomy.py` until a registry-backed capability index exists.
- **No `install_confirmation: prompt`** in core API `plan_and_run()` — the
  API is non-interactive by design. The CLI layer handles prompting.

## 0.4.1 — Security & Correctness

**Behavioral change:** `run_tool(mode="auto")` now always executes via
subprocess isolation, regardless of trust level. This makes the
documented isolation guarantee true by default. `mode="direct"` remains
available as an explicit opt-in for performance-critical workloads that
knowingly share in-process globals.

**Migration note:** Tools that rely on shared in-process state
(module-level globals, process-wide singletons) should explicitly pass
`mode="direct"` going forward.

### Fixes

- **AsyncAgentNode /v1 base URL** — the async client now appends `/v1` to
  `base_url` when missing, matching `AgentNode` (sync). Previously all
  `AsyncAgentNode` calls hit `/packages/...` and 404ed against
  production. (P0-04)
- **AgentNodeClient.install()** now POSTs
  `POST /v1/packages/{slug}/install` so the backend tracks the install
  event. Previously installs went untracked. (P0-05)
- **run_tool(mode="auto") always uses subprocess** — see behavioral
  change above. (P0-06)
- **Response parsing hardening** — `_handle`/`_request` no longer crash
  on non-dict JSON error bodies or HTML/plain-text 2xx responses; both
  are now surfaced as `AgentNodeError`. (P1-SDK3, P1-SDK4)
- **run_tool reserved kwargs** — passing the internal `entry` kwarg via
  `**kwargs` now raises `TypeError` instead of silently shadowing the
  dispatcher's forwarding path. (P1-SDK5)
- **Installer download ceiling** — `download_artifact` now enforces a
  500 MB hard ceiling (`MAX_DOWNLOAD_BYTES`). Declared
  `Content-Length` is checked up front; streamed bytes are checked per
  chunk. Oversized downloads are aborted and the partial file removed.
  (P1-SDK6)
- **run_tool dispatch logging** — `runner.run_tool` now emits an `INFO`
  log line with the resolved runtime and mode, so callers can confirm
  what mode `auto` actually picked without inspecting the
  `RunToolResult` after the fact. (P1-SDK10)
