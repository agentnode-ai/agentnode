import type { Metadata } from "next";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "AgentNode Guard";
const DESCRIPTION = "The pre-execution policy gateway: how Guard checks trust, permissions and environment, decides allow/deny/prompt, and audits every decision.";
const PATH = "/docs/guard";

export const metadata: Metadata = {
  title: "AgentNode Guard — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "AgentNode Guard — Docs | AgentNode",
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
            <p className="mb-4 text-sm leading-relaxed text-muted">
              AgentNode Guard is the pre-execution policy gateway built into
              the SDK. Every install and run call passes through Guard before
              anything executes. Guard checks trust levels, permission
              boundaries, and environment context &mdash; then allows, denies,
              or prompts. Every decision is logged to an append-only audit
              trail.
            </p>

            <SubHeading>How it works</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Guard sits at the center of every execution path: the Python
              SDK, the CLI, the MCP adapter, and the agent runtime. There is
              no way to run or install a pack without Guard evaluating the
              request first.
            </p>
            <div className="mb-6 grid gap-4 sm:grid-cols-3">
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-2 font-mono text-xs font-bold text-primary">
                  1. Check
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  Guard reads your config (<C>~/.agentnode/config.json</C>),
                  the package&apos;s trust level and permissions, and the
                  runtime environment (secrets present? CI? container?).
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-2 font-mono text-xs font-bold text-primary">
                  2. Decide
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  Based on your policy, Guard returns one of three actions:
                  <span className="font-medium text-green-400"> allow</span>,
                  <span className="font-medium text-red-400"> deny</span>, or
                  <span className="font-medium text-yellow-400"> prompt</span>.
                  Broken or missing config defaults to deny (fail-closed).
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-2 font-mono text-xs font-bold text-primary">
                  3. Audit
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  Every decision is logged to <C>~/.agentnode/audit.jsonl</C> with
                  timestamp, event type, package, action, source, and environment
                  context. The audit trail is append-only and auto-rotated.
                </p>
              </div>
            </div>

            <SubHeading>Enforcement points</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Guard is enforced at every execution path. There is no bypass.
            </p>
            <DocTable
              headers={["Path", "Check", "Enforcement"]}
              rows={[
                ["client.install()", "check_install", "Hard — policy crash = deny"],
                ["runner.run_tool()", "check_run", "Hard — deny or prompt stops execution"],
                ["runtime.handle()", "check_run", "Hard — returns policy_denied error"],
                ["MCP call_tool()", "check_run", "Hard — fail-closed (non-interactive = deny)"],
                ["agent_runner", "trust check", "Hard — own trust verification"],
                ["remote_runner", "dispatcher", "Hard — audited as remote_run event"],
              ]}
            />

            <SubHeading>Policy configuration</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Guard reads your policy from <C>~/.agentnode/config.json</C>.
              These are the key settings:
            </p>
            <CodeBlock title="~/.agentnode/config.json">{`{
  "trust": {
    "minimum_trust_level": "verified"
  },
  "permissions": {
    "network": "prompt",
    "filesystem": "prompt",
    "code_execution": "sandboxed"
  },
  "audit": {
    "max_size_mb": 10,
    "max_files": 5
  }
}`}</CodeBlock>
            <DocTable
              headers={["Setting", "Values", "Description"]}
              rows={[
                ["trust.minimum_trust_level", "unverified, verified, trusted, curated", "Packages below this level are denied"],
                ["permissions.network", "allow, prompt, deny", "How to handle packages requesting network access"],
                ["permissions.filesystem", "allow, prompt, deny", "How to handle packages requesting filesystem access"],
                ["permissions.code_execution", "sandboxed, prompt, deny", "How to handle packages requesting code execution"],
                ["audit.max_size_mb", "number (default: 10)", "Rotate audit log when it exceeds this size"],
                ["audit.max_files", "number (default: 5)", "Maximum number of rotated audit files to keep"],
              ]}
            />

            <SubHeading>Environment-aware decisions</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Guard detects your runtime environment and escalates decisions
              when risk is higher:
            </p>
            <ul className="mb-4 list-inside list-disc space-y-2 text-sm text-muted">
              <li>
                <span className="font-medium text-foreground/80">
                  Secrets detected
                </span>{" "}
                &mdash; if environment variables like <C>AWS_*</C>,{" "}
                <C>OPENAI_*</C>, or <C>DATABASE_URL</C> are present, Guard
                escalates to prompt for unverified packages with network access
              </li>
              <li>
                <span className="font-medium text-foreground/80">CI mode</span>{" "}
                &mdash; detected via <C>CI</C>, <C>GITHUB_ACTIONS</C>, etc.
                Non-interactive environments use deny instead of prompt
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  Strict mode
                </span>{" "}
                &mdash; set <C>AGENTNODE_GUARD_STRICT=true</C> to force all
                uncertain decisions to deny instead of prompt
              </li>
            </ul>

            <SubHeading>Viewing the audit trail</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Every Guard decision is logged. Use the CLI to inspect:
            </p>
            <CodeBlock title="terminal">{`# Show recent decisions
$ agentnode audit show

# Show statistics
$ agentnode audit stats

# Export as JSON for analysis
$ agentnode audit show --limit 100 --json > decisions.json`}</CodeBlock>
          </section>
      </DocsShell>
    </>
  );
}
