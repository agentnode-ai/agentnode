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

const TITLE = "Credentials & Connectors";
const DESCRIPTION =
  "Where AgentNode stores your API keys: OS keychain or a local 0600 file, never the cloud. Env vars override the vault; keys never enter sandboxed code.";
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

const PROVIDER_ROWS = [
  ["openai", "OPENAI_API_KEY", "Yes"],
  ["anthropic", "ANTHROPIC_API_KEY", "Yes"],
  ["openrouter", "OPENROUTER_API_KEY", "Yes"],
  ["deepseek", "DEEPSEEK_API_KEY", "Yes"],
  ["mistral", "MISTRAL_API_KEY", "Yes"],
  ["qwen", "DASHSCOPE_API_KEY", "Yes"],
  ["gemini", "GEMINI_API_KEY", "Yes"],
  ["ollama", "OLLAMA_API_KEY", "No — local, keyless"],
];

export default function Page() {
  return (
    <>
      <DocsJsonLd title={TITLE} description={DESCRIPTION} path={PATH} />
      <DocsShell title={TITLE}>
        <section>
          <p className="text-sm leading-relaxed text-muted">
            AgentNode keeps your API keys on your own machine — in the OS
            keychain when one is available, otherwise a local file with
            owner-only permissions. Keys are never uploaded to AgentNode; they
            are sent only to the provider you configured. Environment variables
            always override stored credentials, and keys never enter sandboxed
            community code.
          </p>
        </section>

        <section>
          <SectionHeading id="where-stored">Where are credentials stored?</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            <C>agentnode auth set</C> stores a token through a two-tier vault:
          </p>
          <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              <span className="text-foreground">OS keychain (primary)</span> —
              Windows Credential Manager, macOS Keychain, or Linux Secret
              Service via <C>keyring</C>. The secret lives only in the keychain;{" "}
              <C>~/.agentnode/credentials.json</C> keeps non-secret metadata.
            </li>
            <li>
              <span className="text-foreground">Local file (fallback)</span> —
              when no keychain is available (e.g. headless Linux or CI), the
              token is written to <C>~/.agentnode/credentials.json</C> with{" "}
              <C>0600</C> permissions. This is a plaintext file — never
              described as encrypted.
            </li>
          </ul>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            Honest scope: the keychain protects against other local users and
            accidental file exposure. Neither tier protects a secret from a
            program already running as you.
          </p>
        </section>

        <section>
          <SectionHeading id="env-vs-vault">Environment variables vs the vault</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            An environment variable always wins over a stored credential, so
            explicit and CI intent takes precedence. AgentNode also loads{" "}
            <C>~/.agentnode/.env</C> as part of the environment.
          </p>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            For <span className="text-foreground">LLM providers</span>, the env
            var name comes from the provider registry (see the table below). For{" "}
            <span className="text-foreground">connector packages</span>, the
            convention is <C>AGENTNODE_CRED_&lt;PROVIDER&gt;</C> (uppercase),
            and the resolution order is configurable via{" "}
            <C>credentials.resolve_mode</C>:
          </p>
          <DocTable
            headers={["resolve_mode", "Sources checked, in order"]}
            rows={[
              ["auto (default)", "Environment variable → local vault → server-side"],
              ["env", "Environment variable only"],
              ["local", "Local vault only"],
              ["api", "Server-side only"],
            ]}
          />
        </section>

        <section>
          <SectionHeading id="providers">LLM provider keys</SectionHeading>
          <p className="mb-4 text-sm leading-relaxed text-muted">
            These providers are built in. Set a key with <C>agentnode auth set
            &lt;provider&gt;</C>, or export its environment variable. Ollama is
            local and keyless.
          </p>
          <DocTable
            headers={["Provider", "Environment variable", "Needs key"]}
            rows={PROVIDER_ROWS}
          />
          <p className="mt-3 text-sm leading-relaxed text-muted">
            Any other OpenAI-compatible endpoint can be added under{" "}
            <C>llm.providers</C> in your config — see{" "}
            <Link href="/docs/llm-providers" className="text-primary hover:underline">
              LLM Providers
            </Link>
            .
          </p>
        </section>

        <section>
          <SectionHeading id="auth-commands">Auth commands</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            Tokens are entered hidden (never via the command line) and only ever
            shown masked. No AgentNode account is required to store or use them.
          </p>
          <CodeBlock title="terminal">{`$ agentnode auth set openai      # prompts for the key (hidden input)
$ agentnode auth set github      # also works for connector packages
$ agentnode auth list            # configured credentials (no secrets shown)
$ agentnode auth status          # providers + effective source (env vs stored)
$ agentnode auth test openai     # validate a key via a free endpoint
$ agentnode auth remove github   # delete a stored credential`}</CodeBlock>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            <C>agentnode auth test ollama</C> is a keyless reachability check
            against your local Ollama — it does not validate a key.
          </p>
        </section>

        <section>
          <SectionHeading id="setup-wizard">Setup wizard</SectionHeading>
          <p className="text-sm leading-relaxed text-muted">
            <C>agentnode setup</C> can store one provider key for you as part of
            its interactive flow. It offers this — it never requires it — and
            uses the same vault, hidden input, and honest storage labels as{" "}
            <C>agentnode auth set</C>. You can always skip it and configure keys
            later.
          </p>
        </section>

        <section>
          <SectionHeading id="sandboxes-and-secrets">Sandboxes and secrets</SectionHeading>
          <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              Untrusted community code runs in a sandbox with{" "}
              <span className="text-foreground">no host environment</span> —
              your keys and tokens are never passed in. A community MCP that
              declares it needs credentials is refused, not handed a secret.
            </li>
            <li>
              Sandboxed agents reach an LLM through a{" "}
              <span className="text-foreground">host-side broker</span>: the
              provider key stays on the host and never enters the container,
              audit records, manifests, or lockfiles.
            </li>
            <li>
              Trusted and curated packages run host-side. In the default
              subprocess mode they still receive only an allowlisted environment
              — API keys and tokens are stripped. The opt-in <C>mode=&quot;direct&quot;</C>{" "}
              shares your full process environment, so use it only for code you
              trust. These tiers are policy-checked, not OS-sandboxed.
            </li>
          </ul>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            More:{" "}
            <Link href="/docs/sandbox" className="text-primary hover:underline">
              Execution sandbox
            </Link>{" "}
            and the{" "}
            <Link href="/docs/security" className="text-primary hover:underline">
              security model
            </Link>
            .
          </p>
        </section>

        <section>
          <SectionHeading id="connectors">Connector packages</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            Some packages call external APIs (GitHub, Slack, etc.). They never
            receive a raw token — instead they get a{" "}
            <span className="text-foreground">CredentialHandle</span> that
            attaches the secret only for allowed requests:
          </p>
          <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              <span className="text-foreground">Domain-locked</span> — the
              handle checks the target host against the connector&apos;s
              allowed domains and refuses non-HTTPS targets before any secret is
              attached.
            </li>
            <li>
              <span className="text-foreground">Not serializable</span> — a
              handle cannot be pickled or printed; <C>repr()</C> shows only the
              provider name, never the secret.
            </li>
            <li>
              <span className="text-foreground">Resolved per policy</span> —
              from <C>AGENTNODE_CRED_&lt;PROVIDER&gt;</C>, the local vault, or
              the server-side source, in the order set by{" "}
              <C>credentials.resolve_mode</C>.
            </li>
          </ul>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            Store a connector token the same way as any provider:{" "}
            <C>agentnode auth set &lt;provider&gt;</C>.
          </p>
        </section>

        <section>
          <SectionHeading id="troubleshooting">Troubleshooting</SectionHeading>
          <DocTable
            headers={["Symptom", "Fix"]}
            rows={[
              ["Stored as plaintext file, not keychain", "Expected when no OS keychain is available (headless Linux, CI). The file is 0600. agentnode auth list shows the storage backend."],
              ["Key not picked up", "An environment variable overrides the vault. Run agentnode auth status to see the effective source for each provider."],
              ["Unknown provider", "Check the spelling against the provider table, or add a custom endpoint under llm.providers (see LLM Providers)."],
              ["Connector request rejected", "The CredentialHandle blocks hosts outside the connector's allowed domains and any non-HTTPS target."],
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
              — install, set up, and run your first capability.
            </li>
            <li>
              <Link href="/docs/llm-providers" className="text-primary hover:underline">
                LLM Providers
              </Link>{" "}
              — every supported provider, including custom endpoints and Ollama.
            </li>
            <li>
              <Link href="/docs/sandbox" className="text-primary hover:underline">
                Execution Sandbox
              </Link>{" "}
              — how untrusted code is isolated from your secrets.
            </li>
            <li>
              <Link href="/docs/security" className="text-primary hover:underline">
                Security Model
              </Link>{" "}
              — what is enforced per trust tier.
            </li>
            <li>
              <Link href="/getting-started" className="text-primary hover:underline">
                Getting Started
              </Link>{" "}
              — the guided overview.
            </li>
          </ul>
        </section>
      </DocsShell>
    </>
  );
}
