import type { Metadata } from "next";
import {
  DocsShell,
  DocsJsonLd,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "CLI Reference";
const DESCRIPTION = "Reference for the AgentNode command line: setup, search, install, run, publish, validate, doctor, audit, auth and more — with flags and examples.";
const PATH = "/docs/cli";

export const metadata: Metadata = {
  title: "CLI Reference — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "CLI Reference — Docs | AgentNode",
    description: DESCRIPTION,
    type: "website",
    url: PATH,
    siteName: "AgentNode",
  },
};

export default function Page() {
  return (
    <>
      <DocsJsonLd title={TITLE} description={DESCRIPTION} path={PATH} />
      <DocsShell title={TITLE}>
          <section>
            <p className="mb-6 text-sm text-muted">
              Complete reference for all 18 commands in the AgentNode CLI.
            </p>

            {/* setup */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode setup
              </h4>
              <p className="mb-3 text-sm text-muted">
                Run the setup wizard to configure your local environment.
                Sets up defaults for trust policy, credential storage, and
                other settings.
              </p>
              <CodeBlock title="terminal">{`$ agentnode setup`}</CodeBlock>
            </div>

            {/* search */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode search &lt;query&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Search the registry for packages matching a text query.
              </p>
              <DocTable
                headers={["Flag", "Description"]}
                rows={[
                  ["--framework <name>", "Filter by framework (langchain, crewai, generic)"],
                  ["--trust <level>", "Minimum trust level (unverified, verified, trusted, curated)"],
                  ["--runtime <name>", "Filter by runtime (python)"],
                  ["--capability <id>", "Filter by capability ID"],
                  ["--publisher <slug>", "Filter by publisher namespace"],
                  ["--limit <n>", "Maximum results (default: 20)"],
                  ["--json", "Output as JSON"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode search "email automation" --framework langchain --trust verified`}</CodeBlock>
              </div>
            </div>

            {/* resolve */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode resolve &lt;capability_id...&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Resolve one or more capability IDs to ranked package
                recommendations using the scoring engine.
              </p>
              <DocTable
                headers={["Flag", "Description"]}
                rows={[
                  ["--framework <name>", "Preferred framework for scoring"],
                  ["--trust <level>", "Minimum trust level"],
                  ["--json", "Output as JSON"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode resolve pdf_extraction email_sending --framework crewai`}</CodeBlock>
              </div>
            </div>

            {/* install */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode install &lt;slug&gt; [slug...]
              </h4>
              <p className="mb-3 text-sm text-muted">
                Install one or more packs. Supports version pinning with{" "}
                <C>slug@version</C>.
              </p>
              <CodeBlock title="terminal">{`$ agentnode install pdf-reader-pack
$ agentnode install pdf-reader-pack@1.1.0
$ agentnode install pdf-reader-pack web-search-pack`}</CodeBlock>
            </div>

            {/* update */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode update &lt;slug&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Update an installed pack to its latest version.
              </p>
              <CodeBlock title="terminal">{`$ agentnode update pdf-reader-pack
Updating pdf-reader-pack 1.2.0 -> 1.3.0... done`}</CodeBlock>
            </div>

            {/* rollback */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode rollback &lt;slug&gt;@&lt;version&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Roll back an installed pack to a specific previous version.
              </p>
              <CodeBlock title="terminal">{`$ agentnode rollback pdf-reader-pack@1.2.0
Rolling back pdf-reader-pack 1.3.0 -> 1.2.0... done`}</CodeBlock>
            </div>

            {/* info */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode info &lt;slug&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Display detailed metadata about a package: version history,
                publisher, trust level, permissions, capabilities, and
                compatibility.
              </p>
              <CodeBlock title="terminal">{`$ agentnode info pdf-reader-pack`}</CodeBlock>
            </div>

            {/* explain */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode explain &lt;slug&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Explain a package in plain language: what it does, what
                permissions it requires, which frameworks it supports, and
                typical use cases. Designed for deciding whether to install.
              </p>
              <CodeBlock title="terminal">{`$ agentnode explain pdf-reader-pack`}</CodeBlock>
            </div>

            {/* audit */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode audit
              </h4>
              <p className="mb-3 text-sm text-muted">
                View and manage the{" "}
                <a href="/docs/guard" className="text-primary hover:underline">
                  AgentNode Guard
                </a>{" "}
                policy decision audit trail. Every install and run decision
                is logged to <C>~/.agentnode/audit.jsonl</C>.
              </p>
              <DocTable
                headers={["Subcommand", "Description"]}
                rows={[
                  ["audit show", "Show recent audit entries (default: last 20)"],
                  ["audit show --limit 50", "Show more entries"],
                  ["audit show --json", "Output raw JSON for scripting"],
                  ["audit stats", "Summary: allow/deny/prompt counts, top packages"],
                  ["audit clear --yes", "Delete the audit log"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode audit show
TIMESTAMP            EVENT             SLUG                      ACTION    SOURCE                  TRUST
───────────────────────────────────────────────────────────────────────────────────────────────────────────
2026-04-16 14:23:01  client_install    pdf-reader-pack           allow     default                 trusted
2026-04-16 14:23:05  run_tool          pdf-reader-pack           allow     default                 trusted
2026-04-16 14:25:12  client_install    untrusted-pack            deny      trust_level             unverified

$ agentnode audit stats
  Total entries:  142
  Period:         2026-04-10 → 2026-04-16
  Actions:   allow  118  (83.1%)   deny  19  (13.4%)   prompt  5  (3.5%)`}</CodeBlock>
              </div>
            </div>

            {/* doctor */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode doctor
              </h4>
              <p className="mb-3 text-sm text-muted">
                Analyze your local setup, check for outdated packs, missing
                dependencies, configuration issues, and suggest improvements.
              </p>
              <CodeBlock title="terminal">{`$ agentnode doctor
Checking environment...
  Python:     3.11.5      OK
  SDK:        0.5.2       OK
  Config:     found       OK
  Lockfile:   found       OK

  1 suggestion: pdf-reader-pack (1.2.0 -> 1.3.0 available)`}</CodeBlock>
            </div>

            {/* list */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode list
              </h4>
              <p className="mb-3 text-sm text-muted">
                Show all locally installed packs with versions and trust levels.
              </p>
              <CodeBlock title="terminal">{`$ agentnode list
Installed packages:
  pdf-reader-pack    v1.0.0  trusted
  web-search-pack    v1.0.0  trusted
  email-drafter-pack v1.0.0  verified`}</CodeBlock>
            </div>

            {/* publish */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode publish [directory]
              </h4>
              <p className="mb-3 text-sm text-muted">
                Publish a pack to the registry. Validates the manifest, builds
                the artifact, and uploads to the AgentNode registry. Server-side
                verification (security scanning, quality gate, indexing) runs
                after upload.
              </p>
              <DocTable
                headers={["Flag", "Description"]}
                rows={[
                  ["--dry-run", "Validate and build artifact without uploading"],
                  ["--skip-validate", "Continue past validation errors (does not skip hard failures like missing manifest)"],
                  ["--token <key>", "API key for authentication (overrides AGENTNODE_API_KEY env var)"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode publish .
$ agentnode publish ./my-pack --dry-run
$ agentnode publish . --token ank_your_key`}</CodeBlock>
              </div>
            </div>

            {/* init */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode init [name]
              </h4>
              <p className="mb-3 text-sm text-muted">
                Scaffold a new package from a template. Creates a Gold-ready project structure
                with manifest, source code, tests, and verification cases pre-configured.
              </p>
              <DocTable
                headers={["Flag", "Description"]}
                rows={[
                  ["--type <type>", "Template type: local, api, file, or agent"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode init my-api-connector --type api
Created my-api-connector/
  agentnode.yaml, pyproject.toml, src/, tests/, fixtures/
  Template: api (with VCR cassette support)
  Next: implement your tool, then run agentnode record-cases .`}</CodeBlock>
              </div>
            </div>

            {/* validate */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode validate [directory]
              </h4>
              <p className="mb-3 text-sm text-muted">
                Validate a package manifest offline. Checks syntax, required fields,
                verification cases, cassette file existence, and predicts the maximum
                achievable tier (Gold eligibility).
              </p>
              <CodeBlock title="terminal">{`$ agentnode validate .

Validating my-pack@1.0.0
  [PASS] Manifest syntax valid
  [PASS] Required fields present
  [PASS] Verification cases defined (2 cases)
  [PASS] Cassette files exist

  Max tier              Gold
  Mode                  fixture`}</CodeBlock>
            </div>

            {/* record-cases */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode record-cases [directory]
              </h4>
              <p className="mb-3 text-sm text-muted">
                Record VCR cassettes for API verification cases. Makes real API calls and
                saves responses as YAML fixtures. These cassettes are replayed during
                verification for deterministic testing without network access.
              </p>
              <DocTable
                headers={["Flag", "Description"]}
                rows={[
                  ["--strict", "Exit with error if cassettes contain secrets or possible tokens"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode record-cases .

Recording cassettes for my-api-pack
  [OK] search_query -> fixtures/cassettes/search.yaml
  [OK] empty_query -> fixtures/cassettes/empty.yaml

  Cassette Warnings
  [DYNAMIC] Fields that may change between runs:
    - interactions[0].response.headers.Date

  Next: agentnode verify-local .`}</CodeBlock>
              </div>
              <div className="mt-3 rounded-md border border-warning/20 bg-warning/5 px-3 py-2 text-xs text-muted">
                Review cassettes for leaked credentials before committing. The{" "}
                <C>--strict</C> flag blocks on detected secrets and can be used in CI.
              </div>
            </div>

            {/* verify-local */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode verify-local [directory]
              </h4>
              <p className="mb-3 text-sm text-muted">
                Run the full verification pipeline locally. Simulates exactly what the
                server does: install, import, smoke test (run cases), pytest, contract
                validation, reliability, and determinism scoring. Shows the predicted
                tier and score.
              </p>
              <CodeBlock title="terminal">{`$ agentnode verify-local .

Verifying my-pack@1.0.0

  Pipeline
  [PASS] Install      Installed in venv
  [PASS] Import       Import OK
  [PASS] Smoke        ok
  [PASS] Tests        4 passed in 0.5s
  [PASS] Contract
  [PASS] Reliability  100.0%
  [PASS] Determinism  100.0%

  Score                 95/95
  Tier                  Gold
  Mode                  fixture

  This package will reach Gold tier after publishing.`}</CodeBlock>
            </div>

            {/* report */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode report &lt;slug&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Generate a full security report for a package, including trust
                history, scan results, dependency analysis, and permission
                audit.
              </p>
              <CodeBlock title="terminal">{`$ agentnode report pdf-reader-pack`}</CodeBlock>
            </div>

            {/* recommend */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode recommend
              </h4>
              <p className="mb-3 text-sm text-muted">
                Analyze your installed packs and suggest additional capabilities
                that complement your current setup.
              </p>
              <CodeBlock title="terminal">{`$ agentnode recommend
Based on your installed packs, you might also need:
  document_summary    -> document-summarizer-pack   trusted
  data_visualization  -> data-visualizer-pack       verified`}</CodeBlock>
            </div>

            {/* resolve-upgrade */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode resolve-upgrade
              </h4>
              <p className="mb-3 text-sm text-muted">
                Find higher-scored or more trusted alternatives for your
                currently installed packs.
              </p>
              <CodeBlock title="terminal">{`$ agentnode resolve-upgrade
Checking for upgrades...
  pdf-extractor-pack -> pdf-reader-pack (higher trust, better score)`}</CodeBlock>
            </div>

            {/* policy-check */}
            <div className="mb-8 rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode policy-check &lt;slug&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Check whether a package meets specified policy constraints.
              </p>
              <DocTable
                headers={["Flag", "Description"]}
                rows={[
                  ["--trust <level>", "Required minimum trust level"],
                  ["--no-network", "Require no network access"],
                  ["--no-code-execution", "Require no code execution"],
                  ["--no-filesystem-write", "Require no filesystem write access"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode policy-check pdf-reader-pack --trust trusted --no-network`}</CodeBlock>
              </div>
            </div>

            {/* auth */}
            <div className="rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode auth &lt;provider&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Store a local API token for a connector provider (e.g. GitHub,
                Slack). Tokens are saved to{" "}
                <C>~/.agentnode/credentials.json</C> with 0600 permissions.
                No AgentNode account required.
              </p>
              <DocTable
                headers={["Subcommand / Flag", "Description"]}
                rows={[
                  ["auth <provider>", "Store a token for the given provider (interactive prompt)"],
                  ["auth list", "List locally stored credentials"],
                  ["auth remove <provider>", "Remove a locally stored credential"],
                  ["--validate", "Validate the token against the provider's API before saving"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode auth github --validate
? Paste your GitHub token: ********
✓ Token validated — stored for github`}</CodeBlock>
              </div>
            </div>

            {/* credentials */}
            <div className="rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode credentials &lt;subcommand&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Manage server-side OAuth credentials stored in the AgentNode
                backend. These are obtained via OAuth flows and proxied through
                the API — your tool never sees the raw token.
              </p>
              <DocTable
                headers={["Subcommand / Flag", "Description"]}
                rows={[
                  ["credentials list", "List all server-side credentials"],
                  ["credentials test <id>", "Test connectivity for a credential"],
                  ["credentials delete <id>", "Revoke and delete a credential"],
                  ["--json", "Output as JSON (available on all subcommands)"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode credentials list
Credentials (2):
  a1b2c3d4  github     active  domains=[api.github.com]
  e5f6g7h8  slack      active  domains=[slack.com]`}</CodeBlock>
              </div>
            </div>

            {/* import */}
            <div className="rounded-lg border border-border bg-card p-5">
              <h4 className="mb-1 font-mono text-sm font-bold text-primary">
                agentnode import &lt;file&gt; --from &lt;platform&gt;
              </h4>
              <p className="mb-3 text-sm text-muted">
                Import existing tools from other frameworks and generate an ANP
                manifest automatically. See{" "}
                <a
                  href="/docs/import"
                  className="text-primary hover:underline"
                >
                  Import Tools
                </a>{" "}
                for full details.
              </p>
              <DocTable
                headers={["Flag", "Description"]}
                rows={[
                  ["--from <platform>", "Source platform: mcp, langchain, openai, crewai, clawhub, skillssh"],
                  ["--output <dir>", "Output directory for generated manifest (default: current directory)"],
                ]}
              />
              <div className="mt-3">
                <CodeBlock title="terminal">{`$ agentnode import my_tools.py --from langchain`}</CodeBlock>
              </div>
            </div>
          </section>
      </DocsShell>
    </>
  );
}
