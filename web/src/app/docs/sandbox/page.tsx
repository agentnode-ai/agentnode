import type { Metadata } from "next";
import Link from "next/link";
import { DocsShell, DocsJsonLd, C, CodeBlock } from "@/components/docs";

const TITLE = "Execution Sandbox";
const DESCRIPTION =
  "How AgentNode runs community packages in a hardened container sandbox — isolated or not at all. Setup via agentnode sandbox pull and doctor.";
const PATH = "/docs/sandbox";

export const metadata: Metadata = {
  title: "Execution Sandbox — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Execution Sandbox — Docs | AgentNode",
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
            Since SDK 0.11, community-tier code — toolpack builds, toolpack
            runs, and MCP servers from <C>verified</C>/<C>unverified</C>{" "}
            publishers — executes inside a hardened container sandbox{" "}
            <strong className="text-foreground">or not at all</strong>. There
            is no host fallback: if no container runtime or pinned sandbox
            image is available, execution is blocked. Since SDK 0.12,
            community <em>agents</em> can run the same way behind an explicit
            opt-in flag (default OFF), with provider API keys staying
            host-side.
          </p>

          <p className="mb-4 text-sm leading-relaxed text-muted">
            Setting up takes two commands (Docker or Podman required):
          </p>
          <CodeBlock title="terminal">{`$ agentnode sandbox pull     # fetch the digest-pinned sandbox image (one time)
$ agentnode sandbox doctor   # diagnose readiness / explain why a package is blocked`}</CodeBlock>

          <p className="mt-4 mb-4 text-sm leading-relaxed text-muted">
            <C>trusted</C>/<C>curated</C> packages are unaffected — they run on
            the host with subprocess isolation, no sandbox required.
          </p>

          <div className="rounded-lg border border-primary/20 bg-primary/5 p-4">
            <p className="text-sm text-muted">
              The complete sandbox guide (architecture, network allowlists,
              agent <C>llm_access</C>, audit records, troubleshooting) is
              being written. Until then, the{" "}
              <Link href="/docs/security" className="text-primary hover:underline">
                Security Model
              </Link>{" "}
              page documents exactly what the sandbox enforces, and{" "}
              <C>agentnode sandbox doctor</C> explains your local state.
            </p>
          </div>
        </section>
      </DocsShell>
    </>
  );
}
