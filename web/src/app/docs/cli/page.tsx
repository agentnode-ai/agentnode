import type { Metadata } from "next";
import Link from "next/link";
import {
  DocsShell,
  DocsJsonLd,
  SectionHeading,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "CLI Reference";
const DESCRIPTION =
  "The AgentNode CLI reference, grouped by task: setup, search, install, run, auth, guard, sandbox, publish and more — with real flags and examples.";
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

const SETUP_ROWS = [
  ["agentnode setup", "Interactive wizard covering all first-class settings as multiple-choice prompts with recommended defaults: install behavior, trust level, permissions, guard posture, credentials, sandbox and host_trust_policy, plus an advanced gate. Skippable — accepting the recommendations reproduces today's config."],
  ["agentnode config list", "Show all settings with their descriptions and allowed values."],
  ["agentnode config get <key>", "Read one config value."],
  ["agentnode config set <key> <value>", "Set a config value, e.g. config set llm.default_provider ollama."],
  ["agentnode config set sandbox.host_trust_policy <default|curated_only|none>", "Choose which trust tiers may run directly on your host; stricter values sandbox trusted/curated (fail-closed, reinstall may be needed)."],
  ["agentnode doctor [--fix] [--json]", "Diagnose your setup and detect missing capabilities. --fix auto-installs suggestions."],
  ["agentnode reset", "Reset the local configuration to defaults."],
];

const FIND_ROWS = [
  ["agentnode search <query>", "Full-text search of the registry."],
  ["agentnode discover [--trending] [--new] [--category <c>] [--type toolpack|agent]", "Browse trending, newly published, or categorized packages."],
  ["agentnode resolve <capability...> [--framework <f>] [--json]", "Rank packages for one or more capability IDs using the scoring engine."],
  ["agentnode recommend [--json]", "Suggest packages that complement what you already have installed."],
  ["agentnode install <slug> [--version <v>] [--yes]", "Install a package: policy check, download, SHA-256 verify, lockfile update."],
  ["agentnode run <slug | task> [--input <json>] [--file <path>] [--explain] [--dry-run] [--raw] [--json]", "Run an installed tool (by slug) or a natural-language task. Runs in subprocess isolation."],
  ["agentnode remove <slug> [--yes]", "Remove an installed package from the lockfile."],
];

const INSPECT_ROWS = [
  ["agentnode inspect <slug> [--json]", "Security report for an installed package: trust, permissions, risk, signature, policy/guard preview."],
  ["agentnode capabilities", "List installed packages. capabilities show <name> prints details for one."],
  ["agentnode skill list", "List installed skills. skill show <slug> [--render KEY=VALUE] [--raw] renders a skill prompt."],
  ["agentnode logs [run_id] [--limit <n>] [--json]", "Show agent run logs, or details for one run_id."],
  ["agentnode audit [--limit <n>] [--event <e>] [--slug <s>] [--action allow|deny|prompt] [--tool <t>] [--json]", "Read the Guard/policy decision trail from ~/.agentnode/audit.jsonl."],
];

const TRUST_ROWS = [
  ["agentnode auth set <provider> [--from-env <VAR>]", "Store a provider credential (entered hidden). --from-env imports an existing env var."],
  ["agentnode auth list", "Show configured credentials (no secrets shown)."],
  ["agentnode auth status", "Show every provider and its effective source (env vs stored)."],
  ["agentnode auth test <provider>", "Validate a key via a free endpoint; for Ollama, a localhost reachability check."],
  ["agentnode auth remove <provider>", "Delete a stored credential."],
  ["agentnode guard policy|status [--json]", "Show the resolved guard policy, or the recent guard activity summary."],
  ["agentnode guard set <action> <allow|prompt|deny> [--tool <key>]", "Set a guard action policy, globally or per tool."],
  ["agentnode guard unset [<action>] --tool <key>", "Remove a per-tool guard override (one action, or all for that tool)."],
  ["agentnode guard check <slug/tool> [--action <a>] [--json]", "Dry-run the guard decision for a tool without executing it."],
  ["agentnode guard reset", "Reset guard policies to defaults."],
  ["agentnode lock seal [--force]", "Compute integrity hashes for all lockfile entries."],
  ["agentnode lock verify [--strict] [--online] [--json]", "Verify lockfile integrity; --online checks publisher key status against the registry."],
  ["agentnode sandbox pull", "Explicitly pull the digest-pinned sandbox image (no auto-pull)."],
  ["agentnode sandbox doctor [<slug>] [--json]", "Diagnose sandbox readiness (now host-trust-policy aware), optionally for one package."],
  ["agentnode sandbox status", "One-line sandbox readiness check."],
];

const PUBLISH_ROWS = [
  ["agentnode init [name] [--type local|api|file|agent|skill]", "Scaffold a new package from a template."],
  ["agentnode validate [path]", "Validate a manifest offline; check cases and predict the maximum achievable tier."],
  ["agentnode record-cases [path] [--strict]", "Record VCR cassettes for API verification cases. --strict fails on detected secrets."],
  ["agentnode verify-local [path]", "Run the full verification pipeline locally (install, import, smoke, tests, scoring)."],
  ["agentnode publish [path] [--dry-run] [--skip-validate] [--token <key>] [--yes]", "Publish a package to the registry. --yes is required for non-interactive/CI publish."],
  ["agentnode mcp doctor <slug> [--skip-start] [--json]", "Check an MCP server's readiness."],
  ["agentnode mcp verify [path] [--test] [--json]", "Verify an MCP agentnode.yaml manifest; --test runs a protocol test."],
  ["agentnode mcp submit [path] [--test] [--token <key>] [--dry-run]", "Submit an MCP server for catalog review."],
  ["agentnode mcp status <submission_id> [--token <key>] [--json]", "Check the status of an MCP submission."],
];

export default function Page() {
  return (
    <>
      <DocsJsonLd title={TITLE} description={DESCRIPTION} path={PATH} />
      <DocsShell title={TITLE}>
        <section>
          <p className="text-sm leading-relaxed text-muted">
            The <C>agentnode</C> CLI ships with the SDK (<C>pip install
            agentnode-sdk</C>). Commands are grouped by task below. Running{" "}
            <C>agentnode</C> with no command shows a status dashboard; global
            flags <C>--version</C> and <C>--no-color</C> apply to every command.
          </p>
          <p className="mt-4 mb-3 text-sm leading-relaxed text-muted">
            The five you will use most:
          </p>
          <CodeBlock title="terminal">{`$ agentnode setup                  # one-time interactive setup
$ agentnode search "pdf"           # find a capability
$ agentnode install pdf-reader-pack
$ agentnode run pdf-reader-pack --input '{"file_path":"report.pdf"}'
$ agentnode auth status            # check configured providers`}</CodeBlock>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            New here? Start with the{" "}
            <Link href="/docs/quickstart" className="text-primary hover:underline">
              Quick Start
            </Link>{" "}
            — this page is the full reference.
          </p>
        </section>

        <section>
          <SectionHeading id="setup">Setup &amp; configuration</SectionHeading>
          <DocTable headers={["Command", "Description"]} rows={SETUP_ROWS} />
        </section>

        <section>
          <SectionHeading id="find-install-run">Find, install &amp; run</SectionHeading>
          <DocTable headers={["Command", "Description"]} rows={FIND_ROWS} />
        </section>

        <section>
          <SectionHeading id="inspect-history">Inspect &amp; history</SectionHeading>
          <DocTable headers={["Command", "Description"]} rows={INSPECT_ROWS} />
        </section>

        <section>
          <SectionHeading id="trust-safety">Trust &amp; safety</SectionHeading>
          <DocTable headers={["Command", "Description"]} rows={TRUST_ROWS} />
        </section>

        <section>
          <SectionHeading id="build-publish">Build &amp; publish</SectionHeading>
          <DocTable headers={["Command", "Description"]} rows={PUBLISH_ROWS} />
          <p className="mt-3 text-sm leading-relaxed text-muted">
            Full publishing walkthrough:{" "}
            <Link href="/docs/publishing" className="text-primary hover:underline">
              Publishing Guide
            </Link>{" "}
            and{" "}
            <Link href="/docs/verification" className="text-primary hover:underline">
              Package Verification
            </Link>
            .
          </p>
        </section>

        <section>
          <SectionHeading id="advanced">Advanced</SectionHeading>
          <DocTable
            headers={["Command", "Description"]}
            rows={[
              ["agentnode serve", "Run AgentNode itself as an MCP server over stdio."],
            ]}
          />
        </section>

        <section>
          <SectionHeading id="related">Related</SectionHeading>
          <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              <Link href="/docs/quickstart" className="text-primary hover:underline">
                Quick Start
              </Link>{" "}
              — the guided first-run path.
            </li>
            <li>
              <Link href="/docs/credentials" className="text-primary hover:underline">
                Credentials &amp; Connectors
              </Link>{" "}
              — details behind <C>agentnode auth</C>.
            </li>
            <li>
              <Link href="/docs/sandbox" className="text-primary hover:underline">
                Execution Sandbox
              </Link>{" "}
              — details behind <C>agentnode sandbox</C>.
            </li>
            <li>
              <Link href="/docs/guard" className="text-primary hover:underline">
                AgentNode Guard
              </Link>{" "}
              — details behind <C>agentnode guard</C> and <C>agentnode audit</C>.
            </li>
            <li>
              <Link href="/docs/python-sdk" className="text-primary hover:underline">
                Python SDK
              </Link>{" "}
              — the programmatic equivalent of these commands.
            </li>
          </ul>
        </section>
      </DocsShell>
    </>
  );
}
