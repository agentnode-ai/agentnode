import type { Metadata } from "next";
import Link from "next/link";
import {
  DocsShell,
  DocsJsonLd,
  C,
  CodeBlock,
  SectionHeading,
  DocTable,
} from "@/components/docs";

const TITLE = "AgentNode Sandbox";
const DESCRIPTION =
  "How AgentNode isolates code per trust tier: untrusted community code runs in a hardened container or not at all (fail-closed), while trusted/curated packages run host-side with policy checks. Covers container hardening, network rules, the agent sandbox, and the real setup commands.";
const PATH = "/docs/sandbox";

export const metadata: Metadata = {
  title: "AgentNode Sandbox — Execution Isolation per Trust Tier | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "AgentNode Sandbox — Execution Isolation per Trust Tier | AgentNode",
    description: DESCRIPTION,
    type: "website",
    url: PATH,
    siteName: "AgentNode",
  },
};

const MATRIX_HEADERS = [
  "Tier / kind",
  "Default execution",
  "Isolation",
  "Network",
  "Secrets",
  "Fail-closed?",
  "Notes",
];

const MATRIX_ROWS = [
  [
    "curated",
    "Host",
    "Subprocess + env filtering",
    "Policy-checked, not OS-enforced",
    "Env allowlist",
    "n/a (host)",
    "AgentNode-owned, vetted",
  ],
  [
    "trusted",
    "Host",
    "Subprocess + env filtering",
    "Policy-checked, not OS-enforced",
    "Env allowlist",
    "n/a (host)",
    "Vetted third-party; host run is a transition, target is sandboxed",
  ],
  [
    "verified",
    "Container or refused",
    "Hardened container",
    "none by default, unknown = deny",
    "None (PYTHONPATH only)",
    "Yes",
    "Community code",
  ],
  [
    "unverified",
    "Container or refused",
    "Hardened container",
    "none by default, unknown = deny",
    "None (PYTHONPATH only)",
    "Yes",
    "Community code",
  ],
  [
    "unknown / missing",
    "Container or refused",
    "Hardened container",
    "none by default, unknown = deny",
    "None",
    "Yes",
    "Treated as community, never host",
  ],
  [
    "MCP servers",
    "Container or refused",
    "Hardened container (FS, host, secrets)",
    "default network (npx/uvx) — exception",
    "None; env_keys refused",
    "Yes",
    "Network not isolated; FS / secret / host isolation applies",
  ],
  [
    "agents",
    "Host (trusted/curated); container if flag ON",
    "Host thread/process, or container",
    "Container: none; host: not OS-enforced",
    "Host-side LLM broker; keys never enter container",
    "Yes (community, flag ON)",
    "Default OFF; community agents refused unless enabled",
  ],
];

export default function Page() {
  return (
    <>
      <DocsJsonLd title={TITLE} description={DESCRIPTION} path={PATH} />
      <DocsShell title={TITLE}>
        <section>
          <p className="text-sm leading-relaxed text-muted">
            AgentNode runs untrusted, community-tier code (verified and
            unverified publishers, plus any unknown tier) inside a hardened
            container —{" "}
            <strong className="text-foreground">or not at all</strong>. Trusted
            and curated packages run on the host with subprocess isolation and
            policy checks, not OS-level sandboxing. Docker or Podman plus a
            digest-pinned image are required; there is no silent host fallback.
          </p>
        </section>

        <section>
          <SectionHeading id="who-runs-where">Who runs where</SectionHeading>
          <p className="mb-6 text-sm leading-relaxed text-muted">
            What gets enforced depends on the package trust level and the kind
            of code. The matrix below is the single source of truth — there is
            no blanket rule that every package is sandboxed.
          </p>
          <DocTable headers={MATRIX_HEADERS} rows={MATRIX_ROWS} />
          <p className="mt-4 text-sm leading-relaxed text-muted">
            The trust-tier rows describe how a package&apos;s code executes by
            default; the <C>MCP servers</C> and <C>agents</C> rows note where
            those runtimes differ. Community-tier code is never downgraded to
            host execution.
          </p>
        </section>

        <section>
          <SectionHeading id="container-hardening">
            Container hardening
          </SectionHeading>
          <p className="mb-4 text-sm leading-relaxed text-muted">
            When community code runs in the sandbox, the container is locked
            down: read-only root filesystem, non-root user, all Linux
            capabilities dropped, no privilege escalation, and CPU / memory /
            PID limits. No host paths are mounted, no host environment is
            passed, and the image is referenced by an immutable digest that is
            never pulled automatically.
          </p>
          <CodeBlock title="what AgentNode runs (illustrative)">{`docker run -i \\
  --rm --read-only --cap-drop=ALL \\
  --security-opt=no-new-privileges \\
  --user 1000:1000 \\
  --pids-limit 256 --memory 512m --cpus 1 \\
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \\
  -e HOME=/sandbox-home --tmpfs /sandbox-home:rw,size=16m \\
  --network none \\
  -v agentnode-pack-<slug>-<version>-<hash>:/pack:ro \\
  -e PYTHONPATH=/pack \\
  ghcr.io/agentnode-ai/sandbox@sha256:6c77...  python -c <wrapper>`}</CodeBlock>
          <p className="mt-4 text-sm leading-relaxed text-muted">
            The only thing mounted is the pre-built, per-pack-version volume
            (read-only) whose name is tied to the exact verified artifact, so a
            stale or mismatched build can never be silently reused.
          </p>
        </section>

        <section>
          <SectionHeading id="network">Network rules</SectionHeading>
          <p className="text-sm leading-relaxed text-muted">
            Toolpack and agent-sandbox containers start with{" "}
            <C>--network none</C>. A network is granted only when the package
            declares a recognized network level; unknown, missing, or{" "}
            <C>none</C> values mean no socket at all — an unknown value is never
            a silent grant (unknown = deny).
          </p>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            <strong className="text-foreground">
              MCP server containers are the exception.
            </strong>{" "}
            They may use the default network because npx- and uvx-based servers
            often fetch packages at startup. Network isolation is therefore
            stricter for toolpacks and agent sandboxes than for MCP server
            processes — but filesystem, host, and secret isolation still apply
            to MCP containers.
          </p>
        </section>

        <section>
          <SectionHeading id="fail-closed">
            Fail-closed: isolated or not at all
          </SectionHeading>
          <p className="text-sm leading-relaxed text-muted">
            Community execution is fail-closed end to end. If a container
            runtime or the pinned image is missing, or a toolpack build volume
            is missing or stale, execution is blocked before any code runs.
            There is never a silent downgrade to host execution.
          </p>
          <div className="mt-4 rounded-lg border border-primary/20 bg-primary/5 p-4">
            <p className="text-sm text-muted">
              <C>agentnode sandbox doctor</C> explains exactly why something is
              blocked and what to do next. With <C>--json</C> or no TTY it
              performs no action — it only reports.
            </p>
          </div>
        </section>

        <section>
          <SectionHeading id="setup">Setup &amp; diagnosis</SectionHeading>
          <p className="mb-4 text-sm leading-relaxed text-muted">
            Three commands manage and diagnose the sandbox. The image is only
            ever fetched by an explicit pull — there is no auto-pull anywhere in
            the SDK.
          </p>
          <CodeBlock title="terminal">{`$ agentnode sandbox status          # one-line readiness check (no changes)
$ agentnode sandbox doctor          # full diagnosis + guidance
$ agentnode sandbox doctor <slug>   # check one installed package
$ agentnode sandbox pull            # explicitly fetch the digest-pinned image`}</CodeBlock>
        </section>

        <section>
          <SectionHeading id="agent-sandbox">
            Agent sandbox &amp; LLM keys
          </SectionHeading>
          <p className="text-sm leading-relaxed text-muted">
            Running an agent means running the agent&apos;s own code, so it is
            gated harder. Trusted and curated agents run on the host. Community
            agents are refused unless you opt in: the agent sandbox is{" "}
            <strong className="text-foreground">default OFF</strong>.
          </p>
          <CodeBlock title="opt in (default OFF)">{`$ agentnode config set agent_sandbox.enabled true
# or, per shell:  export AGENTNODE_AGENT_SANDBOX=1`}</CodeBlock>
          <p className="mt-4 text-sm leading-relaxed text-muted">
            When enabled, verified and unverified community agents run
            sandbox-or-fail-closed in the same hardened container. LLM calls go
            through a host-side broker: the provider API key stays on the host
            and never enters the container, audit records, manifests, or
            lockfiles. Access is default-deny — an agent reaches the host LLM
            only if its manifest declares <C>llm_access.enabled: true</C>, and
            the host-config ceiling always wins. Tool calls are routed back to
            the host and limited to the agent&apos;s declared allowlist.
          </p>
        </section>

        <section>
          <SectionHeading id="honest-limits">Honest limits</SectionHeading>
          <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              Trusted and curated packages are not OS-sandboxed — their network
              and filesystem declarations are policy-checked, not enforced by
              the OS.
            </li>
            <li>
              MCP server containers can reach the network by default (the
              documented exception above).
            </li>
            <li>
              The agent sandbox is opt-in and default OFF; with it off,
              community agents are refused, not run on the host.
            </li>
            <li>
              A container runtime (Docker or Podman) and the digest-pinned image
              are required; without them, community code is blocked, not
              downgraded.
            </li>
            <li>
              The sandbox complements publisher signatures, lockfile integrity,
              and Guard — it does not replace them.
            </li>
          </ul>
        </section>

        <section>
          <SectionHeading id="related">Related</SectionHeading>
          <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              <Link href="/docs/security" className="text-primary hover:underline">
                Security Model
              </Link>{" "}
              — what is enforced per trust tier, end to end.
            </li>
            <li>
              <Link href="/docs/credentials" className="text-primary hover:underline">
                Credentials &amp; Connectors
              </Link>{" "}
              — OS-keychain storage and host-side secret handling.
            </li>
            <li>
              <Link href="/docs/agents" className="text-primary hover:underline">
                Agents
              </Link>{" "}
              — agent tiers, manifests, and <C>llm_access</C>.
            </li>
            <li>
              <Link href="/docs/llm-runtime" className="text-primary hover:underline">
                LLM Runtime
              </Link>{" "}
              — how agents and the runtime call models.
            </li>
            <li>
              <Link href="/docs/guard" className="text-primary hover:underline">
                AgentNode Guard
              </Link>{" "}
              — pre-execution action policy and rate limits.
            </li>
            <li>
              <Link href="/docs/verification" className="text-primary hover:underline">
                Package Verification
              </Link>{" "}
              — how trust tiers are earned.
            </li>
          </ul>
        </section>
      </DocsShell>
    </>
  );
}
