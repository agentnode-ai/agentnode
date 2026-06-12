import type { Metadata } from "next";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "Trust & Security";
const DESCRIPTION = "AgentNode's four trust levels, how packages progress between them, security scanning, publisher signatures, and the permission model.";
const PATH = "/docs/trust";

export const metadata: Metadata = {
  title: "Trust & Security — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Trust & Security — Docs | AgentNode",
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
              Trust is not binary. AgentNode provides a layered trust model
              where every pack has a clear, auditable trust level that
              progresses over time through verification, community usage, and
              manual review.
            </p>

            <SubHeading>The four trust levels</SubHeading>
            <div className="mb-6 grid gap-4 sm:grid-cols-2">
              <div className="rounded-lg border border-gray-500/30 bg-card p-4">
                <p className="mb-2 font-mono text-sm font-bold text-gray-400">
                  Unverified
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  Newly published pack. Metadata has been validated and the
                  manifest is syntactically correct, but no further review has
                  been performed. Use with caution in production.
                </p>
              </div>
              <div className="rounded-lg border border-blue-500/30 bg-card p-4">
                <p className="mb-2 font-mono text-sm font-bold text-blue-400">
                  Verified
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  Publisher identity has been confirmed. The pack passes
                  automated security scans (Bandit), and its declared
                  permissions are consistent with actual behavior. Publisher has
                  2FA enabled.
                </p>
              </div>
              <div className="rounded-lg border border-green-500/30 bg-card p-4">
                <p className="mb-2 font-mono text-sm font-bold text-green-400">
                  Trusted
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  Security scanned with zero findings, tests pass, active
                  maintenance history, meaningful community usage, and no
                  reported issues. The pack has demonstrated reliability over
                  time.
                </p>
              </div>
              <div className="rounded-lg border border-primary/30 bg-card p-4">
                <p className="mb-2 font-mono text-sm font-bold text-primary">
                  Curated
                </p>
                <p className="text-sm leading-relaxed text-muted">
                  Manually reviewed by the AgentNode team. Code has been
                  audited, permissions verified against actual behavior, and the
                  pack meets the highest quality bar. This is the highest
                  assurance level in the registry.
                </p>
              </div>
            </div>

            <SubHeading>How to progress through trust levels</SubHeading>
            <DocTable
              headers={["From", "To", "Requirements"]}
              rows={[
                ["Unverified", "Verified", "Confirm publisher identity, enable 2FA, pass Bandit security scan, permissions match declared behavior"],
                ["Verified", "Trusted", "Zero security findings, tests pass, active maintenance, community usage, no unresolved reports"],
                ["Trusted", "Curated", "Manual review by AgentNode team, code audit, permissions verification, documentation review"],
              ]}
            />

            <SubHeading>Security scanning</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Every pack published to the registry undergoes automated security
              scanning:
            </p>
            <ul className="mb-4 list-inside list-disc space-y-2 text-sm text-muted">
              <li>
                <span className="font-medium text-foreground/80">
                  Bandit analysis
                </span>{" "}
                -- static analysis for common Python security issues (hardcoded
                passwords, SQL injection, insecure deserialization, etc.)
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  Ed25519 signatures
                </span>{" "}
                -- every published pack is signed with the publisher&apos;s key.
                Install-time verification ensures the pack has not been tampered
                with after publication.
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  Typosquatting detection
                </span>{" "}
                -- the registry detects package names that are suspiciously
                similar to popular packs (e.g., <C>pdf-reeder-pack</C> vs.{" "}
                <C>pdf-reader-pack</C>) and flags them for manual review.
              </li>
              <li>
                <span className="font-medium text-foreground/80">
                  Hash verification
                </span>{" "}
                -- SHA-256 hashes are computed at publish time and verified at
                install time. If the hash does not match, the installation is
                aborted.
              </li>
            </ul>

            <SubHeading>The permission model</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Every pack must explicitly declare its permissions across four
              dimensions. Agents and users see the full permission manifest
              before installation, and the resolution engine can filter by
              permission constraints.
            </p>
            <DocTable
              headers={["Dimension", "Levels", "Description"]}
              rows={[
                ["Network", "none, restricted, unrestricted", "What network access the pack requires. \"none\" means no outbound calls. \"restricted\" means specific domains only. \"unrestricted\" means any network access."],
                ["Filesystem", "none, temp, read, write", "What file system access the pack requires. \"temp\" means temporary directory only. \"read\" means it reads files. \"write\" means it reads and writes."],
                ["Code Execution", "none, sandboxed, full", "Whether the pack executes arbitrary code. \"sandboxed\" means restricted execution environment. \"full\" means unrestricted."],
                ["Data Access", "input_only, output_only, bidirectional", "The direction of data flow. \"input_only\" means the pack reads input but does not send data externally. \"bidirectional\" means it both receives and sends data."],
              ]}
            />

            <SubHeading>Inspecting a package</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Use <C>agentnode info</C> and <C>agentnode policy-check</C> to
              review a package&apos;s trust level, permissions, and whether
              it meets your policy constraints before installation.
            </p>
            <CodeBlock title="terminal">{`$ agentnode info pdf-reader-pack
$ agentnode policy-check pdf-reader-pack --trust trusted --no-network`}</CodeBlock>
          </section>
      </DocsShell>
    </>
  );
}
