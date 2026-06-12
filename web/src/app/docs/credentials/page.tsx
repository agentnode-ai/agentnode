import type { Metadata } from "next";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "Credentials & Connectors";
const DESCRIPTION = "How connector packages authenticate: local tokens, server-side OAuth, environment variables, and AgentNode's credential resolution chain.";
const PATH = "/docs/credentials";

export const metadata: Metadata = {
  title: "Credentials & Connectors — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Credentials & Connectors — Docs | AgentNode",
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
              Some packages connect to external APIs (GitHub, Slack, etc.).
              These <strong>connector packages</strong> need credentials to
              authenticate. AgentNode provides two ways to manage credentials:
              local tokens you own, and server-side OAuth via the AgentNode
              backend.
            </p>

            <SubHeading>How credential resolution works</SubHeading>
            <p className="mb-3 text-sm text-muted">
              When a connector package runs, the SDK resolves credentials
              through a configurable chain. By default (<C>auto</C> mode),
              it tries each source in order until it finds a match:
            </p>
            <div className="mb-6 grid gap-4 sm:grid-cols-3">
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-2 font-mono text-xs font-bold text-primary">
                  1. Environment Variable
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  <C>AGENTNODE_CRED_GITHUB</C>, <C>AGENTNODE_CRED_SLACK</C>,
                  etc. Best for CI/CD pipelines and containers.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-2 font-mono text-xs font-bold text-primary">
                  2. Local Credential File
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  <C>~/.agentnode/credentials.json</C> — tokens stored
                  via <C>agentnode auth</C>. No account needed.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-2 font-mono text-xs font-bold text-primary">
                  3. Server-Side (API)
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  OAuth2 tokens managed by the AgentNode backend. Secrets
                  never leave the server &mdash; requests are proxied.
                </p>
              </div>
            </div>

            <SubHeading>Local credentials (agentnode auth)</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Use <C>agentnode auth</C> to store your own API tokens locally.
              No AgentNode account required. Tokens are validated against the
              provider&apos;s API before being saved.
            </p>
            <CodeBlock title="terminal">{`# Store a GitHub token
$ agentnode auth github
  Create a token at: https://github.com/settings/tokens
  Recommended scopes: repo, read:user

Paste your token: ********
Validating token... valid
GitHub credential stored.

# Store a Slack token
$ agentnode auth slack

# List stored credentials
$ agentnode auth list

# Remove a credential
$ agentnode auth remove github`}</CodeBlock>
            <p className="mt-3 text-sm text-muted">
              Credentials are stored in <C>~/.agentnode/credentials.json</C>{" "}
              with file permissions restricted to the current user (0600 on
              Unix). This follows the same pattern used by <C>gh</C>,{" "}
              <C>docker</C>, and <C>aws</C> CLI tools.
            </p>

            <SubHeading>Server-side credentials (agentnode credentials)</SubHeading>
            <p className="mb-3 text-sm text-muted">
              For OAuth2 flows that require a callback URL, the AgentNode
              backend handles the token exchange. Your secrets never leave the
              server &mdash; tool execution is proxied through the backend.
            </p>
            <CodeBlock title="terminal">{`# List server-side credentials
$ agentnode credentials list

# Test connectivity
$ agentnode credentials test <id>

# Revoke a credential
$ agentnode credentials delete <id>`}</CodeBlock>

            <SubHeading>Environment variables</SubHeading>
            <p className="mb-3 text-sm text-muted">
              For CI/CD and automated environments, set credentials as
              environment variables. The naming convention
              is <C>AGENTNODE_CRED_</C> followed by the provider name in
              uppercase:
            </p>
            <CodeBlock title="terminal">{`export AGENTNODE_CRED_GITHUB=ghp_xxxxxxxxxxxxx
export AGENTNODE_CRED_SLACK=xoxb-xxxxxxxxxxxxx`}</CodeBlock>

            <SubHeading>Resolution mode</SubHeading>
            <p className="mb-3 text-sm text-muted">
              You can control which sources are checked
              via <C>credentials.resolve_mode</C> in your config:
            </p>
            <DocTable
              headers={["Mode", "Sources Checked", "Use Case"]}
              rows={[
                ["auto (default)", "env → local file → API", "Best for development — tries everything"],
                ["env", "Environment variables only", "CI/CD pipelines, containers"],
                ["local", "Local file only", "Offline development, air-gapped systems"],
                ["api", "Server-side only", "Managed environments, OAuth2 flows"],
              ]}
            />
            <CodeBlock title="~/.agentnode/config.json">{`{
  "credentials": {
    "resolve_mode": "auto"
  }
}`}</CodeBlock>

            <SubHeading>Security model</SubHeading>
            <ul className="mb-4 list-inside list-disc space-y-2 text-sm text-muted">
              <li>
                <span className="font-medium text-foreground/80">
                  CredentialHandle
                </span>{" "}
                &mdash; tools never see raw tokens. They receive an opaque
                handle that makes authenticated requests on their behalf.
                The handle validates target domains against the connector&apos;s
                allowed list before attaching credentials.
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  Domain restriction
                </span>{" "}
                &mdash; each credential is bound to specific API domains
                (e.g., <C>api.github.com</C>). Requests to unauthorized
                domains are rejected before any secret is attached.
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  No serialization
                </span>{" "}
                &mdash; CredentialHandles cannot be pickled, serialized, or
                printed. <C>repr()</C> shows only the provider name, never
                the secret.
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  Proxy mode
                </span>{" "}
                &mdash; server-side credentials are never sent to the client.
                Requests are proxied through the backend, so the token stays
                on the server.
              </li>
            </ul>
          </section>
      </DocsShell>
    </>
  );
}
