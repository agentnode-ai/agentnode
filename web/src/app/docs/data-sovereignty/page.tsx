import type { Metadata } from "next";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  C,
} from "@/components/docs";

const TITLE = "Data Sovereignty";
const DESCRIPTION = "What stays on your machine and what the registry sees: local execution, no telemetry, local credentials, audit logs, and offline capability.";
const PATH = "/docs/data-sovereignty";

export const metadata: Metadata = {
  title: "Data Sovereignty — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Data Sovereignty — Docs | AgentNode",
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
              AgentNode is a registry and policy engine &mdash; not a data
              processor. <strong>Your data never touches our
              servers.</strong> Tools run locally in your environment. The
              backend only stores what&apos;s needed for the registry catalog
              (package metadata, account credentials). Everything else stays
              on your machine.
            </p>

            <SubHeading>Your data stays local</SubHeading>
            <ul className="mb-4 list-inside list-disc space-y-2 text-sm text-muted">
              <li>
                <span className="font-medium text-foreground/80">
                  Tool input/output data
                </span>{" "}
                &mdash; the data your agents process never touches our servers.
                Tools run locally in your environment, not on ours.
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  LLM prompts or responses
                </span>{" "}
                &mdash; we have no visibility into what your agent sends to or
                receives from language models.
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  Usage telemetry
                </span>{" "}
                &mdash; the SDK does not phone home. No analytics, no tracking,
                no usage beacons. The only network calls are explicit ones you
                trigger (install, search, resolve).
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  Local credentials
                </span>{" "}
                &mdash; tokens stored via <C>agentnode auth</C> stay
                in <C>~/.agentnode/credentials.json</C> on your machine. They
                are never uploaded.
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  Audit logs
                </span>{" "}
                &mdash; Guard&apos;s audit trail (<C>~/.agentnode/audit.jsonl</C>)
                is local-only. Policy decisions stay on your machine.
              </li>
            </ul>

            <SubHeading>Execution model</SubHeading>
            <p className="mb-3 text-sm text-muted">
              All tool execution happens locally in your Python process. The
              AgentNode backend is only involved in three scenarios:
            </p>
            <div className="mb-6 grid gap-4 sm:grid-cols-3">
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-2 font-mono text-xs font-bold text-primary">
                  Registry Operations
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  Search, install, resolve, publish — catalog operations that
                  transfer package metadata and artifacts.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-2 font-mono text-xs font-bold text-primary">
                  OAuth Proxy
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  Server-side credentials: the backend proxies API calls so
                  OAuth tokens never leave the server. Optional — you can
                  use local tokens instead.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-2 font-mono text-xs font-bold text-primary">
                  Remote Runner
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  For packages that explicitly declare remote execution. The
                  SDK marks these as <C>remote_run</C> in the audit trail.
                  Most packages run locally.
                </p>
              </div>
            </div>

            <SubHeading>Accounts & API keys</SubHeading>
            <p className="mb-3 text-sm text-muted">
              An AgentNode account is needed only for write operations
              (installing, publishing) and server-side credentials. Read-only
              operations like search, info, and explain work without any
              authentication.
            </p>
            <p className="mb-3 text-sm text-muted">
              <strong>API keys</strong> are the primary authentication
              mechanism for the CLI and SDK. They replace session-based login
              for programmatic access:
            </p>
            <ul className="mb-4 list-inside list-disc space-y-2 text-sm text-muted">
              <li>
                <strong>Create</strong> an API key in your account settings on
                the website or via <C>agentnode api-keys create &lt;label&gt;</C>
              </li>
              <li>
                <strong>Set</strong> it as an environment variable
                with <C>export AGENTNODE_API_KEY=ank_...</C> or pass
                via <C>--token</C> flag
              </li>
              <li>
                <strong>Use</strong> it automatically — the CLI reads from
                <C>AGENTNODE_API_KEY</C> env var or <C>~/.agentnode/config.json</C>
              </li>
              <li>
                <strong>Revoke</strong> at any time from your account settings
                — revoked keys are immediately rejected
              </li>
            </ul>
            <p className="mb-3 text-sm text-muted">
              Only a SHA-256 hash of your API key is stored on the server.
              If the database is compromised, the plaintext key cannot be
              recovered. Keys are matched using constant-time comparison
              (<C>hmac.compare_digest</C>) to prevent timing attacks.
            </p>

            <SubHeading>Offline-capable</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Once packages are installed, the SDK works fully offline. The
              lockfile (<C>agentnode.lock</C>) contains all metadata needed to
              run tools without network access. Set{" "}
              <C>credentials.resolve_mode: &quot;local&quot;</C> to ensure
              credential resolution never reaches out to the API. Policy
              enforcement (Guard) is entirely local — no server calls, no
              latency.
            </p>
          </section>
      </DocsShell>
    </>
  );
}
