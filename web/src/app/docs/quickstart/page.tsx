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

const TITLE = "Quick Start";
const DESCRIPTION =
  "Install AgentNode, run setup, choose a provider, and run your first agent skill in minutes. Ollama needs no API key; untrusted code stays sandboxed.";
const PATH = "/docs/quickstart";

export const metadata: Metadata = {
  title: "Quick Start — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Quick Start — Docs | AgentNode",
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
          <p className="text-sm leading-relaxed text-muted">
            Install the SDK, run setup, pick an LLM provider, and run your first
            agent skill — usually in under ten minutes. Ollama works without an
            API key. Security stays fail-closed by default: untrusted community
            code runs sandboxed or not at all, and your credentials never leave
            your machine.
          </p>
        </section>

        <section>
          <SectionHeading id="before-you-start">Before you start</SectionHeading>
          <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              <span className="text-foreground">Python 3.10+</span> and{" "}
              <C>pip</C>.
            </li>
            <li>
              <span className="text-foreground">(Optional) Docker or Podman</span>{" "}
              — needed only to run untrusted community packages, which execute in
              a sandbox. Trusted and curated packages run without it.
            </li>
            <li>
              <span className="text-foreground">(Optional) Ollama</span> — for a
              local provider path that needs no hosted API key.
            </li>
          </ul>
        </section>

        <section>
          <SectionHeading id="install">Install AgentNode</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            One command installs the SDK and the <C>agentnode</C> CLI. No account
            is needed to search, install, or run packages — only publishing
            requires one.
          </p>
          <CodeBlock title="terminal">{`$ pip install agentnode-sdk`}</CodeBlock>
        </section>

        <section>
          <SectionHeading id="run-setup">Run setup</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            The setup wizard is interactive. It lists the LLM providers, can
            optionally store one provider key (entered hidden, never echoed), and
            includes a local-sandbox screen. You can skip it entirely — sensible
            defaults apply. In a non-interactive shell (CI), the credential and
            sandbox prompts skip themselves with guidance, so setup never blocks
            automation.
          </p>
          <CodeBlock title="terminal">{`$ agentnode setup`}</CodeBlock>
        </section>

        <section>
          <SectionHeading id="check-config">Check your configuration</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            Confirm which providers are configured and whether the sandbox is
            ready. Both commands only report — they change nothing.
          </p>
          <CodeBlock title="terminal">{`$ agentnode auth status     # configured LLM providers (incl. Ollama) + connectors
$ agentnode sandbox doctor  # is the sandbox ready, and why a package is blocked`}</CodeBlock>
        </section>

        <section>
          <SectionHeading id="first-capability">Run your first capability</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            Search the registry, install a pack, and run one of its tools — all
            from the CLI.
          </p>
          <CodeBlock title="terminal">{`$ agentnode search "pdf extraction"
$ agentnode install pdf-reader-pack
$ agentnode run pdf-reader-pack --input '{"file_path":"report.pdf"}'`}</CodeBlock>
          <p className="mt-4 mb-3 text-sm leading-relaxed text-muted">
            Or call it from Python. <C>run_tool()</C> runs in an isolated
            subprocess by default.
          </p>
          <CodeBlock title="agent.py" language="python">{`from agentnode_sdk import run_tool

result = run_tool("pdf-reader-pack", file_path="report.pdf")
print(result.result)`}</CodeBlock>
        </section>

        <section>
          <SectionHeading id="ollama">No API key? Use Ollama</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            Ollama is a local, keyless provider. Select it as your default, then
            check that your local Ollama is reachable.
          </p>
          <CodeBlock title="terminal">{`$ agentnode config set llm.default_provider ollama
$ agentnode auth test ollama   # checks your local Ollama is reachable`}</CodeBlock>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            This path needs no hosted API key, but it is not zero-setup: you must
            install Ollama, have it running, and pull a model. AgentNode never
            starts Ollama for you — <C>auth test ollama</C> is only a localhost
            reachability check.
          </p>
        </section>

        <section>
          <SectionHeading id="safe-by-default">Safe by default</SectionHeading>
          <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              Untrusted community code (verified / unverified) runs in a hardened
              container <span className="text-foreground">or not at all</span> —
              there is no silent fallback to host execution.
            </li>
            <li>
              Trusted and curated packages run host-side under policy checks.
            </li>
            <li>
              Credentials stay in your OS keychain and never enter sandboxed
              code.
            </li>
          </ul>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            Details:{" "}
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
          <SectionHeading id="troubleshooting">Troubleshooting</SectionHeading>
          <DocTable
            headers={["Symptom", "Fix"]}
            rows={[
              ["agentnode: command not found", "Activate the Python environment where you installed it (or reinstall: pip install agentnode-sdk)."],
              ["No provider configured", "Run agentnode auth status, then agentnode auth set <provider> — or switch to the keyless Ollama path."],
              ["Ollama not running", "Start Ollama and pull a model, then verify with agentnode auth test ollama."],
              ["Sandbox unavailable", "Run agentnode sandbox doctor; install Docker or Podman and run agentnode sandbox pull. See /docs/sandbox."],
              ["Authentication / key missing", "Store the key with agentnode auth set <provider>, then confirm with agentnode auth test <provider>."],
            ]}
          />
        </section>

        <section>
          <SectionHeading id="related">Related</SectionHeading>
          <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              <Link href="/getting-started" className="text-primary hover:underline">
                Getting Started
              </Link>{" "}
              — the guided overview of how AgentNode works.
            </li>
            <li>
              <Link href="/docs/installation" className="text-primary hover:underline">
                Installation
              </Link>{" "}
              — requirements and environment details.
            </li>
            <li>
              <Link href="/docs/credentials" className="text-primary hover:underline">
                Credentials &amp; Connectors
              </Link>{" "}
              — OS-keychain storage and provider keys.
            </li>
            <li>
              <Link href="/docs/llm-providers" className="text-primary hover:underline">
                LLM Providers
              </Link>{" "}
              — supported providers, including Ollama.
            </li>
            <li>
              <Link href="/docs/sandbox" className="text-primary hover:underline">
                Execution Sandbox
              </Link>{" "}
              — how untrusted code is isolated.
            </li>
            <li>
              <Link href="/search" className="text-primary hover:underline">
                Browse the registry
              </Link>{" "}
              — find a capability to install.
            </li>
          </ul>
        </section>
      </DocsShell>
    </>
  );
}
