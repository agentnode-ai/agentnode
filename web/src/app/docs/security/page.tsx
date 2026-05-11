import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Security Model — How AgentNode Protects Your Environment",
  description:
    "Understand AgentNode's security model: policy checks, subprocess isolation, environment filtering, and what is not yet enforced. Honest documentation for developers.",
  alternates: {
    canonical: "/docs/security",
  },
  openGraph: {
    title: "AgentNode Security Model",
    description:
      "Policy checks, subprocess isolation, environment filtering — and what's not yet enforced. Honest security docs.",
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
      <p className="mt-3 text-muted">
        AgentNode runs third-party tool code on your machine. This page
        explains exactly what is enforced, what is declared but not enforced,
        and what you can do to stay safe.
      </p>

      <SectionTitle>What is enforced</SectionTitle>
      <div className="space-y-2">
        <Row
          label="Policy gate"
          status="enforced"
          description="Every run_tool() call passes through check_run() which evaluates trust level, permissions, and environment context. Returns allow, deny, or prompt. Fail-closed when config is missing."
        />
        <Row
          label="Env filtering"
          status="enforced"
          description="Subprocess mode strips API keys (AWS_*, OPENAI_*, STRIPE_*, etc.) from the child process environment. Only PATH, HOME, PYTHON*, and TEMP are passed through."
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
          description="All policy decisions are logged to ~/.agentnode/audit.jsonl. Append-only, rotated, local-only. Never contains secrets or tool inputs/outputs."
        />
        <Row
          label="Credential domain lock"
          status="enforced"
          description="CredentialHandle validates the target domain against allowed_domains before attaching credentials. Secrets are never exposed via properties."
        />
        <Row
          label="Agent tool allowlist"
          status="enforced"
          description="Agent packages can only invoke tools explicitly listed in their manifest. Attempts to call unlisted tools are blocked."
        />
      </div>

      <SectionTitle>What is NOT enforced</SectionTitle>
      <div className="space-y-2">
        <Row
          label="Network access"
          status="not-enforced"
          description="Permissions like 'network: none' are declared by the publisher and checked by the policy gate, but not sandboxed at runtime. A tool can still make HTTP requests regardless of its declaration."
        />
        <Row
          label="Filesystem access"
          status="not-enforced"
          description="Same as network — declared, policy-checked, but not restricted. A tool with 'filesystem: temp' can still read/write anywhere the process has OS-level access."
        />
        <Row
          label="Direct mode isolation"
          status="not-enforced"
          description="mode='direct' runs tool code in your process with full environment access. This is opt-in only — mode='auto' (the default) always uses subprocess isolation."
        />
        <Row
          label="Input validation"
          status="partial"
          description="The input guard checks for path traversal and suspicious URLs, but only logs warnings — it never blocks execution."
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
          Use <code className="text-xs text-primary">agentnode inspect &lt;slug&gt;</code> to
          review permissions, enforcement status, and policy preview before running a package.
        </li>
        <li>
          Keep the default <code className="text-xs text-primary">mode=&quot;auto&quot;</code> which
          always uses subprocess isolation with env filtering.
        </li>
        <li>
          For sensitive workloads, run tools inside a VM or container for additional isolation.
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
            href="https://github.com/agentnodenet"
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
