import type { Metadata } from "next";
import Link from "next/link";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "Installation";
const DESCRIPTION = "System requirements and install options for the AgentNode SDK, LangChain adapter, and MCP adapter — plus CLI verification and API-key setup.";
const PATH = "/docs/installation";

export const metadata: Metadata = {
  title: "Installation — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Installation — Docs | AgentNode",
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

            <SubHeading>System requirements</SubHeading>
            <DocTable
              headers={["Requirement", "Details"]}
              rows={[
                ["Node.js", "v18 or later (for the CLI)"],
                ["Python", "v3.10 or later (for running packs and using the SDK)"],
                ["npm", "v9 or later"],
                ["OS", "macOS, Linux, Windows (WSL recommended on Windows)"],
              ]}
            />

            <SubHeading>Choose your install</SubHeading>
            <DocTable
              headers={["Use case", "Package", "Install"]}
              rows={[
                ["Build agents & apps in Python", "agentnode-sdk", "pip install agentnode-sdk"],
                ["CLI — search, install, publish", "agentnode-sdk", "pip install agentnode-sdk"],
                ["Use with LangChain / LangGraph", "agentnode-langchain", "pip install agentnode-langchain"],
                ["Use with MCP (Claude, Cursor)", "agentnode-mcp", "pip install agentnode-mcp"],
              ]}
            />

            <SubHeading>Install the CLI</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The AgentNode CLI is included in the Python SDK. After installing
              the SDK, the <C>agentnode</C> command is available in your terminal.
            </p>
            <CodeBlock title="terminal">{`$ pip install agentnode-sdk`}</CodeBlock>

            <p className="mt-4 mb-3 text-sm text-muted">
              Verify the installation:
            </p>
            <CodeBlock title="terminal">{`$ agentnode --version

$ agentnode --help
Usage: agentnode <command> [options]

Commands:
  setup             Run setup wizard
  search            Search for packages
  resolve           Resolve capabilities to packages
  install           Install a package
  remove            Remove a package
  run               Run a capability or natural language task
  auth              Manage local credentials (own API tokens)
  audit             View policy decision audit trail
  doctor            Analyze setup and suggest improvements
  init              Create a new package from template
  validate          Validate a package before publishing
  verify-local      Run verification pipeline locally
  publish           Publish a package to the registry
  record-cases      Record VCR cassettes for verification
  inspect           Security report for an installed package
  discover          Browse trending and categorized packages
  recommend         Get capability recommendations
  capabilities      List installed capabilities
  config            View or modify settings
  logs              Show agent run logs`}</CodeBlock>

            <SubHeading>Authentication & API Keys</SubHeading>
            <p className="mb-3 text-sm text-muted">
              <strong>Who needs an account?</strong> Only publishers (people who
              want to upload packages) and users who install from the registry.
              Read-only operations like{" "}
              <C>search</C>, <C>info</C>, and <C>explain</C> work without any
              authentication.
            </p>
            <DocTable
              headers={["Operation", "Auth Required?", "Who Uses This"]}
              rows={[
                ["search, info, explain", "No", "Anyone — explore the registry freely"],
                ["install, resolve, update", "Yes (API key)", "Developers integrating packages into their agents"],
                ["publish, validate", "Yes (API key + publisher profile)", "Package authors publishing to the registry"],
                ["credentials list/test", "Yes (API key)", "Users managing server-side OAuth credentials"],
                ["auth (local tokens)", "No", "Anyone — local credentials need no account"],
              ]}
            />
            <p className="mt-3 mb-3 text-sm text-muted">
              <strong>How to get an API key:</strong> Register an account
              on the website, then create an API key in your{" "}
              <Link href="/dashboard" className="text-primary hover:underline">
                dashboard
              </Link>
              . The key is shown once at creation — save it immediately.
            </p>
            <CodeBlock title="terminal">{`# Set the API key as an environment variable
$ export AGENTNODE_API_KEY=ank_live_abc123def456

# Or pass it per-command
$ agentnode publish . --token ank_live_abc123def456

# Or store it in config
# ~/.agentnode/config.json: { "api_key": "ank_..." }`}</CodeBlock>
            <p className="mt-3 text-sm text-muted">
              API keys are stored locally in <C>~/.agentnode/config.json</C>.
              The backend stores only a SHA-256 hash of your key — the
              plaintext is never persisted on the server. Keys can be revoked
              at any time from your account settings.
            </p>
          </section>
      </DocsShell>
    </>
  );
}
