import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Security Model — Sandbox, Trust Tiers & Enforcement | AgentNode",
  description:
    "AgentNode's enforced security model: container sandbox for community code (isolated or not at all), publisher signatures, lockfile integrity, OS-keychain credentials — and the honest limits per trust tier.",
  alternates: {
    canonical: "/docs/security",
  },
  openGraph: {
    title: "AgentNode Security Model",
    description:
      "Container sandbox for community code, publisher signatures, lockfile integrity, OS-keychain credentials — enforcement per trust tier, honestly documented.",
    type: "website",
    url: "https://agentnode.net/docs/security",
    siteName: "AgentNode",
  },
  twitter: {
    card: "summary_large_image",
    site: "@AgentNodenet",
  },
};

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mt-10 mb-4 text-xl font-semibold text-foreground">
      {children}
    </h2>
  );
}

function Row({
  label,
  status,
  description,
}: {
  label: string;
  status: "enforced" | "not-enforced" | "partial";
  description: string;
}) {
  const badge =
    status === "enforced" ? (
      <span className="rounded bg-green-500/10 px-2 py-0.5 text-xs font-medium text-green-400">
        Enforced
      </span>
    ) : status === "partial" ? (
      <span className="rounded bg-yellow-500/10 px-2 py-0.5 text-xs font-medium text-yellow-400">
        Partial
      </span>
    ) : (
      <span className="rounded bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-400">
        Not enforced
      </span>
    );

  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-background p-4 sm:flex-row sm:items-start sm:gap-4">
      <div className="flex items-center gap-2 sm:w-48 shrink-0">
        {badge}
        <span className="text-sm font-medium text-foreground">{label}</span>
      </div>
      <p className="text-sm text-muted">{description}</p>
    </div>
  );
}

export default function SecurityPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-12 sm:px-6 lg:px-8">
      <h1 className="text-3xl font-bold text-foreground">Security Model</h1>
      <p className="mt-2 text-xs text-muted/70">
        Applies to SDK 0.16+ · Last updated: 2026-06-12
      </p>
      <p className="mt-3 text-foreground/80 font-medium">
        AgentNode is local-first by design. All tools run on your machine.
        The registry never sees what you do with them.
      </p>
      <p className="mt-3 text-muted">
        AgentNode runs third-party tool code on your machine. This page
        explains exactly what is enforced, what is declared but not enforced,
        and what you can do to stay safe.
      </p>

      <SectionTitle>Enforcement is trust-tiered</SectionTitle>
      <p className="mb-3 text-sm text-muted">
        What gets enforced depends on the package&apos;s trust level:
      </p>
      <div className="space-y-2">
        <div className="rounded-lg border border-border bg-background p-4">
          <p className="text-sm font-medium text-foreground">
            Trusted / curated packages
          </p>
          <p className="mt-1 text-sm text-muted">
            Run on the host with subprocess isolation, environment filtering,
            and timeouts. Their network/filesystem declarations are
            policy-checked, not OS-enforced — that&apos;s the trade these
            tiers earn through review and verification.
          </p>
        </div>
        <div className="rounded-lg border border-green-500/20 bg-green-500/5 p-4">
          <p className="text-sm font-medium text-foreground">
            Verified / unverified community packages
          </p>
          <p className="mt-1 text-sm text-muted">
            Run inside a hardened container sandbox — <span className="text-foreground font-medium">or not at all</span>.
            If no container runtime or pinned sandbox image is available,
            execution is blocked (fail-closed). There is no silent fallback
            to host execution.
          </p>
        </div>
        <div className="rounded-lg border border-border bg-background p-4">
          <p className="text-sm font-medium text-foreground">
            Community agents
          </p>
          <p className="mt-1 text-sm text-muted">
            Gated behind an explicit opt-in flag (default OFF). When enabled,
            community agents run sandbox-or-fail-closed with a host-side LLM
            broker — provider API keys never enter the container. When
            disabled, community agents are refused entirely.
          </p>
        </div>
      </div>

      <SectionTitle>What is enforced</SectionTitle>
      <div className="space-y-2">
        <Row
          label="Container sandbox"
          status="enforced"
          description="Community-tier code (toolpack builds, toolpack runs, MCP servers) executes in a hardened container: read-only filesystem, non-root user, all capabilities dropped, network disabled by default, no host mounts, no secrets. If the sandbox is unavailable, execution is denied — never downgraded to the host."
        />
        <Row
          label="Network allowlist"
          status="enforced"
          description="Community toolpack runs get network access only for explicitly allowlisted domains; unknown or undeclared destinations mean the container runs with networking disabled (unknown = deny)."
        />
        <Row
          label="Pinned sandbox image"
          status="enforced"
          description="The sandbox image is referenced by immutable digest, never by tag, and is never pulled automatically. You fetch it explicitly with 'agentnode sandbox pull'; 'agentnode sandbox doctor' explains the current state."
        />
        <Row
          label="Publisher signatures"
          status="enforced"
          description="Packages are signed with publisher Ed25519 keys. An invalid signature blocks install — no override, no force flag. Missing signatures warn (publisher adoption is gradual) but are never silent."
        />
        <Row
          label="Lockfile integrity"
          status="enforced"
          description="Every lockfile entry carries a SHA-256 integrity hash over its security-critical fields ('agentnode lock seal' / 'lock verify'). Tampered entries are warned on by default and denied before execution in strict mode."
        />
        <Row
          label="LLM keys stay host-side"
          status="enforced"
          description="Sandboxed agents request completions through a host-side broker. Provider API keys never enter the sandbox container, audit records, manifests, or lockfiles."
        />
        <Row
          label="Credential storage"
          status="enforced"
          description="'agentnode auth set' stores keys in the OS keychain (Windows Credential Manager, macOS Keychain, Linux Secret Service). Where no keychain is available (e.g. headless Linux), storage honestly falls back to a plaintext file with 0600 permissions — and tells you so."
        />
        <Row
          label="Policy gate"
          status="enforced"
          description="Every run_tool() call passes through check_run() which evaluates trust level, permissions, and environment context. Returns allow, deny, or prompt. Fail-closed when config is missing."
        />
        <Row
          label="Env filtering"
          status="enforced"
          description="Host subprocess mode (trusted/curated tiers) strips API keys (AWS_*, OPENAI_*, STRIPE_*, etc.) from the child process environment. Only PATH, HOME, PYTHON*, and TEMP are passed through."
        />
        <Row
          label="Subprocess timeout"
          status="enforced"
          description="Tools running in subprocess mode are killed after a configurable timeout (default 30 seconds)."
        />
        <Row
          label="Trust minimum"
          status="enforced"
          description="Your local config sets a minimum trust level (default: verified). Packages below this threshold are denied before execution."
        />
        <Row
          label="CI/non-interactive deny"
          status="enforced"
          description="In CI environments or when AGENTNODE_NON_INTERACTIVE is set, any 'prompt' decision escalates to 'deny'. No silent approvals in automation."
        />
        <Row
          label="Audit trail"
          status="enforced"
          description="All policy decisions are logged to ~/.agentnode/audit.jsonl. Append-only, rotated, local-only. Never contains secrets or tool inputs/outputs. Sandboxed agent runs write one aggregated, sanitized record per run."
        />
        <Row
          label="Credential domain lock"
          status="enforced"
          description="CredentialHandle validates the target domain against allowed_domains before attaching credentials, and refuses non-HTTPS targets. Secrets are never exposed via properties."
        />
        <Row
          label="Agent tool allowlist"
          status="enforced"
          description="Agent packages can only invoke tools explicitly listed in their manifest. Attempts to call unlisted tools are blocked — on the host and across the sandbox RPC boundary."
        />
      </div>

      <SectionTitle>Honest limits — trusted/curated host execution</SectionTitle>
      <p className="mb-3 text-sm text-muted">
        These limits apply to packages that run on the host because they
        earned trusted/curated status. Community-tier code does not have
        these gaps — it runs in the container sandbox or not at all.
      </p>
      <div className="space-y-2">
        <Row
          label="Network access"
          status="not-enforced"
          description="For trusted/curated host execution, 'network: none' declarations are policy-checked but not OS-enforced — a trusted tool could still make HTTP requests. For community packages this IS enforced: the sandbox runs with networking disabled unless allowlisted."
        />
        <Row
          label="Filesystem access"
          status="not-enforced"
          description="Same scope: trusted/curated host tools are policy-checked, not OS-restricted. Community packages run on a read-only container filesystem with no host mounts."
        />
        <Row
          label="Direct mode isolation"
          status="not-enforced"
          description="mode='direct' runs tool code in your process with full environment access. This is opt-in only — mode='auto' (the default) always uses subprocess isolation."
        />
        <Row
          label="Input validation"
          status="partial"
          description="The input guard inspects tool arguments. Path-traversal and URL-anomaly findings require interactive confirmation — and are denied outright in non-interactive/CI contexts. Lower-severity findings (oversized inputs) remain non-blocking warnings."
        />
      </div>

      <SectionTitle>Privacy</SectionTitle>
      <div className="rounded-xl border border-border bg-card p-4 sm:p-6">
        <div className="space-y-3 text-sm text-muted">
          <p>
            <span className="font-medium text-foreground">All execution is local.</span>{" "}
            Tool inputs, outputs, and logs never leave your machine.
          </p>
          <p>
            <span className="font-medium text-foreground">What the registry sees:</span>{" "}
            Install events, search queries, and periodic trust-level refresh requests.
          </p>
          <p>
            <span className="font-medium text-foreground">Audit logs:</span>{" "}
            Stored at <code className="text-xs">~/.agentnode/audit.jsonl</code>.
            Never transmitted. Contains only policy decisions (action, source, reason, trust level).
          </p>
        </div>
      </div>

      <SectionTitle>Recommendations</SectionTitle>
      <ul className="list-disc pl-6 space-y-2 text-sm text-muted">
        <li>
          Set up the sandbox so community packages can run isolated:{" "}
          <code className="text-xs text-primary">agentnode sandbox pull</code> once,{" "}
          <code className="text-xs text-primary">agentnode sandbox doctor</code> whenever
          something is blocked — it explains exactly what is missing.
        </li>
        <li>
          Store provider keys with{" "}
          <code className="text-xs text-primary">agentnode auth set &lt;provider&gt;</code>{" "}
          so they live in the OS keychain instead of environment files.
        </li>
        <li>
          Use <code className="text-xs text-primary">agentnode inspect &lt;slug&gt;</code> to
          review permissions, enforcement status, and policy preview before running a package.
        </li>
        <li>
          Run <code className="text-xs text-primary">agentnode lock verify</code> in CI to
          detect lockfile tampering or drift before it reaches execution.
        </li>
        <li>
          Keep the default <code className="text-xs text-primary">mode=&quot;auto&quot;</code> which
          always uses subprocess isolation with env filtering.
        </li>
        <li>
          For sensitive workloads that rely on <em>trusted/curated host execution</em>, run
          tools inside a VM or container for additional isolation — community-tier code is
          already container-isolated by AgentNode itself.
        </li>
        <li>
          Review <code className="text-xs text-primary">agentnode audit</code> periodically
          to see policy decisions for installed packages.
        </li>
        <li>
          Set a higher minimum trust level in your config if you want stricter package requirements.
        </li>
      </ul>

      <div className="mt-12 rounded-xl border border-border bg-card p-4 text-center text-sm text-muted">
        <p>
          Questions or concerns?{" "}
          <Link href="/faq" className="text-primary hover:underline">
            See our FAQ
          </Link>{" "}
          or reach out on{" "}
          <a
            href="https://github.com/agentnode-ai/agentnode"
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            GitHub
          </a>
          .
        </p>
      </div>
    </main>
  );
}
