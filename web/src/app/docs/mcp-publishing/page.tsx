import type { Metadata } from "next";
import Link from "next/link";
import {
  DocsShell,
  DocsJsonLd,
  SectionHeading,
  SubHeading,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "Publish & verify an MCP server";
const DESCRIPTION =
  "How to list your own MCP server (published on npm or PyPI) in the AgentNode registry: scaffold, verify, submit, prove ownership, the automated sandbox smoke check, status tracking, and review. Listings stay review-gated — nothing is auto-published.";
const PATH = "/docs/mcp-publishing";

export const metadata: Metadata = {
  title: "Publish & verify an MCP server — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Publish & verify an MCP server — Docs | AgentNode",
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
            This guide is for developers who want to <strong>list their own MCP
            server in the AgentNode registry</strong>. If instead you want to{" "}
            <em>use</em> AgentNode as an MCP server inside Claude Code or Cursor,
            see{" "}
            <Link href="/docs/mcp" className="text-primary hover:underline">
              MCP Integration
            </Link>{" "}
            — that is the consumer side and unrelated to publishing.
          </p>
          <p className="mb-4 text-sm leading-relaxed text-muted">
            AgentNode lists MCP servers that are already published on{" "}
            <strong>npm</strong> or <strong>PyPI</strong>. No code is uploaded —
            you point AgentNode at the package with a manifest, and AgentNode
            re-verifies the package, its version, ownership, and (via an automated{" "}
            <strong>sandbox smoke check</strong>) that the server starts and
            answers the MCP protocol. A passing check is a <strong>review
            signal only</strong>: MCP listings stay review-gated and a human
            approves them — <strong>nothing is auto-published</strong>.
          </p>

          <SubHeading>The flow at a glance</SubHeading>
          <CodeBlock title="lifecycle">{`init --type mcp   →  scaffold a manifest
edit + pin        →  set npm/PyPI package, pin the exact version, declare permissions
mcp verify        →  local checks (schema, package/version, permissions)
mcp submit        →  server re-verifies authoritatively; enters review
mcp ownership     →  prove you control the package (publish-challenge)
sandbox smoke     →  AgentNode starts the server in an isolated sandbox
mcp status        →  track review + gate signals
review            →  a human approves; only then can it be published`}</CodeBlock>

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="prerequisites">Prerequisites</SectionHeading>
          <ul className="mb-4 list-inside list-disc space-y-2 text-sm text-muted">
            <li>The AgentNode CLI installed (see{" "}
              <Link href="/docs/installation" className="text-primary hover:underline">
                Installation
              </Link>) and an API key available as <C>AGENTNODE_API_KEY</C> (or
              passed with <C>--token</C>).
            </li>
            <li>A publisher profile on your account (submitting requires one).</li>
            <li>Your MCP server <strong>already published</strong> as a{" "}
              <strong>public</strong> npm or PyPI package, at a concrete
              version (not a floating tag).
            </li>
            <li>A launch command that starts the server over stdio — e.g.{" "}
              <C>npx -y your-pkg@1.2.3</C> (npm) or <C>uvx your-pkg==1.2.3</C>{" "}
              (PyPI).
            </li>
            <li>The source repository that owns the package (used for ownership
              checks).
            </li>
          </ul>

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="init">1 — Scaffold the listing</SectionHeading>
          <p className="mb-3 text-sm text-muted">
            Create a listing project with an MCP manifest and a README:
          </p>
          <CodeBlock title="terminal">{`$ agentnode init --type mcp`}</CodeBlock>
          <p className="mb-3 text-sm text-muted">
            This writes an <C>agentnode.yaml</C> (manifest version <C>0.3</C>,{" "}
            <C>runtime: mcp</C>) with placeholders you must fill in, plus a{" "}
            <C>README.md</C> with the verify → submit steps. Unlike a tool pack,
            an MCP listing uploads no code — it only references the upstream npm/
            PyPI package.
          </p>

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="manifest">2 — The manifest</SectionHeading>
          <p className="mb-3 text-sm text-muted">
            The listing is described by <C>agentnode.yaml</C>. The MCP-specific
            fields live under <C>mcp_server</C>. Fill in exactly one of{" "}
            <C>npm_package</C> / <C>pypi_package</C>, pin the version in{" "}
            <C>command</C>, and set <C>source_repo</C> to the repo that owns the
            package.
          </p>

          <SubHeading>Required fields</SubHeading>
          <DocTable
            headers={["Field", "Example", "Notes"]}
            rows={[
              ["manifest_version", '"0.3"', "Fixed"],
              ["runtime", '"mcp"', "Identifies the listing as an MCP server"],
              ["package_id", '"example-mcp"', "Catalog slug"],
              ["name / summary / description", '"Example MCP"', "Display metadata"],
              ["version", '"1.0.0"', "The listing version (semver)"],
              ["publisher", '"your-publisher-slug"', "Your publisher identifier"],
              ["mcp_server.command", '["npx","-y","example-mcp@1.2.3"]', "Launch command — must PIN the exact version"],
              ["mcp_server.npm_package OR pypi_package", '"example-mcp"', "Exactly one; the other omitted"],
              ["mcp_server.source_repo", '"https://github.com/you/example-mcp"', "Must match the package's registry metadata"],
            ]}
          />

          <SubHeading>Example — npm-backed MCP</SubHeading>
          <CodeBlock title="agentnode.yaml" language="yaml">{`manifest_version: "0.3"
package_id: "example-mcp"
package_type: "toolpack"
name: "Example MCP"
publisher: "your-publisher-slug"
version: "1.0.0"
summary: "An example MCP server listing."
description: |
  Lists an existing npm MCP server in the AgentNode catalog.

runtime: "mcp"
install_mode: "package"
hosting_type: "agentnode_hosted"

mcp_server:
  command: ["npx", "-y", "example-mcp@1.2.3"]   # pin the EXACT version
  npm_package: "example-mcp"
  source_repo: "https://github.com/your-org/example-mcp"

tags: []
categories: []
compatibility:
  frameworks: ["generic"]

# Declare honestly — verification compares this against what the server requests.
permissions:
  network:
    level: "restricted"
    allowed_domains: []
  filesystem:
    level: "none"
  code_execution:
    level: "none"
  data_access:
    level: "input_only"
  user_approval:
    required: "high_risk_only"`}</CodeBlock>

          <SubHeading>Example — PyPI-backed MCP</SubHeading>
          <p className="mb-3 text-sm text-muted">
            Same manifest, but swap the <C>mcp_server</C> block to use{" "}
            <C>pypi_package</C> and a <C>uvx</C> command with a <C>==</C> version
            pin:
          </p>
          <CodeBlock title="agentnode.yaml (mcp_server block)" language="yaml">{`mcp_server:
  command: ["uvx", "example-mcp==1.2.3"]        # pin with ==
  pypi_package: "example-mcp"
  source_repo: "https://github.com/your-org/example-mcp"`}</CodeBlock>
          <p className="mb-3 text-xs text-muted/70">
            The names above are placeholders — replace them with your real
            package and repo.
          </p>

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="pinning">3 — Pin the package version</SectionHeading>
          <p className="mb-3 text-sm text-muted">
            The version must be pinned in <C>mcp_server.command</C> — <C>@1.2.3</C>{" "}
            for npm, <C>==1.2.3</C> for PyPI. An unpinned command (e.g.{" "}
            <C>npx -y example-mcp</C> with no version) is not reproducible and is
            flagged on verify and submit. A verification result is bound to that
            exact package + version + command, so <strong>publishing a new
            package version means re-verifying and re-submitting</strong> the
            listing for the new version.
          </p>

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="verify">4 — Verify locally</SectionHeading>
          <p className="mb-3 text-sm text-muted">
            Run the local checks against your manifest. Add <C>--test</C> to also
            start the server and run the MCP <C>initialize</C> + <C>tools/list</C>{" "}
            handshake locally; add <C>--json</C> to get the machine-readable
            report (this is what <C>mcp submit</C> attaches).
          </p>
          <CodeBlock title="terminal">{`$ agentnode mcp verify .              # schema, package, version, permission checks
$ agentnode mcp verify . --test       # also run the local protocol test
$ agentnode mcp verify . --json       # JSON report (attach on submit)`}</CodeBlock>
          <p className="mb-3 text-sm text-muted">
            The report ends in a status such as <C>TESTED</C> (protocol test
            passed), <C>RESOLVED</C> (package + version resolved),{" "}
            <C>REVIEW_NEEDED</C>, <C>MAINTAINER_ACTION_REQUIRED</C>, or{" "}
            <C>INVALID</C>. The command exits non-zero on <C>INVALID</C>, so it is
            CI-gateable. Local verification is a helpful pre-check — it does{" "}
            <strong>not</strong> replace the server-side re-verification,
            ownership proof, or the sandbox smoke check below.
          </p>

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="submit">5 — Submit for the catalog</SectionHeading>
          <p className="mb-3 text-sm text-muted">
            Submit the manifest and its verification report. Use <C>--dry-run</C>{" "}
            to verify without sending; <C>--test</C> to run the protocol test
            first. Authentication uses <C>--token</C> or <C>AGENTNODE_API_KEY</C>.
          </p>
          <CodeBlock title="terminal">{`$ agentnode mcp submit . --token $AGENTNODE_API_KEY
$ agentnode mcp submit . --dry-run    # verify only, don't send`}</CodeBlock>
          <p className="mb-3 text-sm text-muted">
            On submit, AgentNode <strong>re-verifies the registry facts itself</strong>{" "}
            (package existence, resolved version, repo consistency) — your local
            report is advisory; the server result is authoritative. You get a
            submission <strong>id</strong> and a starting status. A submission
            <strong> never goes live automatically.</strong>
          </p>
          <p className="mb-3 text-sm text-muted">
            You can have only one open submission for the same package + version
            at a time — a second one is rejected as a duplicate until the first
            is resolved.
          </p>

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="ownership">6 — Prove package ownership</SectionHeading>
          <p className="mb-3 text-sm text-muted">
            Ownership is a separate axis from the source-repo match: you prove you
            can <strong>publish</strong> the package with a one-time
            publish-challenge. Issue a challenge, add the returned keyword to your
            package and publish a new version (only someone with publish rights
            can), then verify.
          </p>
          <CodeBlock title="terminal">{`# 1. issue a one-time challenge (token is shown once)
$ agentnode mcp ownership challenge example-mcp --registry npm --token $AGENTNODE_API_KEY

# 2. add the returned keyword to your package metadata, publish a new version

# 3. verify — checks the latest published version for the keyword
$ agentnode mcp ownership verify example-mcp --registry npm --token $AGENTNODE_API_KEY`}</CodeBlock>
          <p className="mb-3 text-sm text-muted">
            <C>verify</C> reports one of:
          </p>
          <DocTable
            headers={["Status", "Meaning", "Next step"]}
            rows={[
              ["verified", "The keyword was found in the latest published version — ownership recorded", "Nothing — you're done (the claim is time-limited; re-verify if it later expires)"],
              ["pending", "Challenge issued, keyword not yet found in a published version", "Publish a new version with the keyword, then verify again"],
              ["expired", "The challenge's time window elapsed", "Issue a new challenge and retry"],
              ["package-not-found", "The package doesn't resolve on the registry", "Check the registry + package name"],
              ["registry-unavailable", "The registry was temporarily unreachable", "Retry verify shortly (transient)"],
            ]}
          />
          <p className="mb-3 text-sm text-muted">
            Verifying ownership publishes nothing on its own — it is one of the
            signals a reviewer needs before a listing can go live.
          </p>

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="sandbox">7 — The automated sandbox smoke check</SectionHeading>
          <p className="mb-3 text-sm text-muted">
            After you submit, AgentNode runs an automated <strong>sandbox smoke
            check</strong> for npm/PyPI MCP submissions. It is a{" "}
            <strong>technical executability + protocol check</strong>, not a
            safety or trust guarantee.
          </p>
          <SubHeading>What it checks</SubHeading>
          <ul className="mb-4 list-inside list-disc space-y-1.5 text-sm text-muted">
            <li>the pinned package can be installed in an isolated sandbox,</li>
            <li>the MCP server starts,</li>
            <li>it answers the MCP <C>initialize</C> flow, and</li>
            <li>it answers tools discovery (<C>tools/list</C>).</li>
          </ul>
          <SubHeading>What it does not do</SubHeading>
          <ul className="mb-4 list-inside list-disc space-y-1.5 text-sm text-muted">
            <li>It is not a full security audit, and it is not a safety or
              trustworthiness certification of the package.</li>
            <li>It does not assess functional quality, and does not guarantee
              every later use will succeed.</li>
          </ul>
          <SubHeading>How it runs</SubHeading>
          <p className="mb-3 text-sm text-muted">
            The check runs with <strong>restricted permissions and bounded
            resources</strong>, with the install phase separate from the run
            phase; the run phase is <strong>network-isolated</strong>, and the
            sandbox is cleaned up automatically afterward.
          </p>
          <SubHeading>Result semantics</SubHeading>
          <DocTable
            headers={["Result", "Meaning", "Effect"]}
            rows={[
              ["passed", "The server started and answered initialize + tools discovery", "A positive review signal (does not auto-publish)"],
              ["failed (startup crash / protocol error / tools/list error)", "The server is objectively broken", "Blocks — fix and resubmit"],
              ["failed (install / timeout / registry issue)", "A transient/environmental problem", "Kept for review; can be rechecked — not an auto-reject"],
              ["deferred (host resources low)", "The host was momentarily low on RAM/disk, so the check was not run", "Kept for review; can be rechecked later"],
              ["unavailable / skipped", "The check could not run (e.g. credentials required)", "Kept for review"],
            ]}
          />
          <p className="mb-3 text-sm text-muted">
            A temporary infrastructure problem never auto-rejects a submission,
            and a passing check never auto-publishes it — it is one input to the
            human review.
          </p>

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="status">8 — Track your submission</SectionHeading>
          <CodeBlock title="terminal">{`$ agentnode mcp status <submission_id> --token $AGENTNODE_API_KEY
$ agentnode mcp status <submission_id> --json`}</CodeBlock>
          <p className="mb-3 text-sm text-muted">
            The status reflects where the submission is in review:
          </p>
          <DocTable
            headers={["Status", "Shown as", "Meaning"]}
            rows={[
              ["pending / quarantined_review", "Under Review", "Held for human review — not live"],
              ["action_required", "Action Required", "A check needs fixing — update and resubmit"],
              ["needs_changes", "Changes Requested", "A reviewer asked for changes"],
              ["approved", "Approved", "Cleared for publication — still not live yet"],
              ["rejected", "Not Accepted", "Not accepted"],
              ["published", "(live)", "Live in the catalog"],
            ]}
          />

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="review">9 — Review & listing</SectionHeading>
          <ul className="mb-4 list-inside list-disc space-y-2 text-sm text-muted">
            <li>MCP submissions remain <strong>review-gated</strong>. The
              technical gates (registry verification, ownership, sandbox smoke)
              are inputs to a human review — they are not the decision.</li>
            <li>Publishers cannot publish their own listing. <strong>Publishing is
              an administrative action</strong>; a passing smoke plus verified
              ownership does not make a listing live on its own.</li>
            <li>Approval (<C>approved</C>) means a reviewer cleared it — it is a
              pre-publish state, still not live, until it is actually published.</li>
            <li>A listing may still require additional editorial or security
              review before it goes live.</li>
          </ul>

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="updates">10 — Changes & new versions</SectionHeading>
          <ul className="mb-4 list-inside list-disc space-y-2 text-sm text-muted">
            <li>Publishing a <strong>new package version</strong> means a new
              listing version: re-run <C>mcp verify</C> and <C>mcp submit</C> for
              it (verification is bound to the exact package + version + command).</li>
            <li>While a submission is <C>action_required</C> or{" "}
              <C>needs_changes</C>, you fix the manifest and re-submit an updated
              verification report for the same package.</li>
            <li>A verified ownership claim is time-limited; if it expires, issue a
              new challenge and verify again.</li>
            <li>Re-running the registry verification on an existing submission
              (for example if a registry was temporarily unavailable) is a
              reviewer action, not a publisher one.</li>
          </ul>

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="troubleshooting">Troubleshooting</SectionHeading>
          <DocTable
            headers={["You see", "Meaning", "Next step"]}
            rows={[
              ["Manifest runtime must be 'mcp'", "runtime is not set to mcp", "Set runtime: \"mcp\""],
              ["Manifest must have mcp_server block", "mcp_server is missing", "Add the mcp_server block with command + npm/pypi_package"],
              ["Package / version not found", "The package or pinned version isn't on the registry", "Publish it, or fix the name/version in command"],
              ["Command is not pinned", "The launch command has no exact version", "Pin @x.y.z (npm) or ==x.y.z (PyPI)"],
              ["Verification report status is not valid / INVALID", "The attached report is missing or INVALID", "Re-run agentnode mcp verify . --test --json"],
              ["An open submission already exists", "A duplicate for the same package + version is in the pipeline", "Wait for it to resolve, or update that submission"],
              ["No publish-challenge to verify", "You ran verify before issuing a challenge", "Run mcp ownership challenge first"],
              ["The challenge has expired", "The challenge window elapsed", "Issue a new challenge and retry"],
              ["Smoke: startup crash / protocol error / tools/list error", "The server is objectively broken in the sandbox", "Fix the server, publish a fixed version, resubmit"],
              ["Smoke: install / timeout / resources", "A transient or environmental issue", "It stays reviewable and can be rechecked — no action needed to avoid rejection"],
            ]}
          />

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="security">Security & trust model</SectionHeading>
          <ul className="mb-4 list-inside list-disc space-y-2 text-sm text-muted">
            <li>The sandbox smoke is a <strong>limited technical check</strong>{" "}
              (does it start and speak the protocol), not a comprehensive security
              certification.</li>
            <li>You remain responsible for the package and code you list.</li>
            <li>Do not put secrets in the manifest. If the server needs
              credentials, declare only the required environment keys — never
              paste tokens or credentials into manifests, reports, or logs.</li>
            <li>A registry listing is not a guarantee of safety; users should
              still review third-party MCP servers before use.</li>
          </ul>

          {/* ---------------------------------------------------------- */}
          <SectionHeading id="quick-reference">Full quick sequence</SectionHeading>
          <CodeBlock title="terminal">{`# 1. scaffold
$ agentnode init --type mcp

# 2. edit agentnode.yaml: set npm_package/pypi_package, pin the version in
#    command, set source_repo, declare permissions

# 3. verify locally
$ agentnode mcp verify . --test --json

# 4. submit (server re-verifies authoritatively; enters review)
$ agentnode mcp submit . --token $AGENTNODE_API_KEY

# 5. prove ownership
$ agentnode mcp ownership challenge <package> --registry npm --token $AGENTNODE_API_KEY
#    ...add the keyword, publish a new version...
$ agentnode mcp ownership verify <package> --registry npm --token $AGENTNODE_API_KEY

# 6. track status (a human reviews; listings stay review-gated)
$ agentnode mcp status <submission_id> --token $AGENTNODE_API_KEY`}</CodeBlock>
          <p className="mt-4 text-sm text-muted">
            For the individual commands see the{" "}
            <Link href="/docs/cli" className="text-primary hover:underline">
              CLI Reference
            </Link>
            . To use AgentNode itself as an MCP server in your editor, see{" "}
            <Link href="/docs/mcp" className="text-primary hover:underline">
              MCP Integration
            </Link>
            .
          </p>
        </section>
      </DocsShell>
    </>
  );
}
