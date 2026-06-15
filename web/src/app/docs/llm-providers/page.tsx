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

const TITLE = "LLM Providers";
const DESCRIPTION =
  "Built-in LLM providers (OpenAI, Anthropic, OpenRouter, DeepSeek, Mistral, Qwen, Gemini), keyless local Ollama, and custom OpenAI-compatible endpoints.";
const PATH = "/docs/llm-providers";

export const metadata: Metadata = {
  title: "LLM Providers — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "LLM Providers — Docs | AgentNode",
    description: DESCRIPTION,
    type: "website",
    url: PATH,
    siteName: "AgentNode",
  },
};

const PROVIDER_ROWS = [
  ["OpenAI", "openai", "API key", "OPENAI_API_KEY", "Official endpoint"],
  ["Anthropic", "anthropic", "API key", "ANTHROPIC_API_KEY", "Official endpoint"],
  ["OpenRouter", "openrouter", "API key", "OPENROUTER_API_KEY", "Gateway to many models; default openai/gpt-4o-mini"],
  ["DeepSeek", "deepseek", "API key", "DEEPSEEK_API_KEY", "OpenAI-compatible; default deepseek-chat"],
  ["Mistral", "mistral", "API key", "MISTRAL_API_KEY", "OpenAI-compatible; default mistral-small-latest"],
  ["Qwen", "qwen", "API key", "DASHSCOPE_API_KEY", "Alibaba DashScope (intl); default qwen-plus"],
  ["Gemini", "gemini", "API key", "GEMINI_API_KEY", "Google OpenAI-compatible endpoint; default gemini-2.0-flash"],
  ["Ollama", "ollama", "None — local", "— (keyless)", "localhost:11434; default llama3.2; opt-in only"],
];

export default function Page() {
  return (
    <>
      <DocsJsonLd title={TITLE} description={DESCRIPTION} path={PATH} />
      <DocsShell title={TITLE}>
        <section>
          <p className="text-sm leading-relaxed text-muted">
            AgentNode binds OpenAI-compatible hosted providers, local Ollama, and
            custom endpoints from a single provider registry. Credentials are
            resolved host-side — through an environment variable or the
            credential vault — and an environment variable always wins. Ollama
            runs locally and needs no API key when it is already running on your
            machine.
          </p>
        </section>

        <section>
          <SectionHeading id="supported">Supported providers</SectionHeading>
          <p className="mb-4 text-sm leading-relaxed text-muted">
            These presets are built in. They are the providers that{" "}
            <C>agentnode setup</C>, <C>agentnode auth</C>, and the host-side LLM
            binding understand.
          </p>
          <DocTable
            headers={["Provider", "Slug", "Auth", "Env var", "Notes"]}
            rows={PROVIDER_ROWS}
          />
          <p className="mt-3 text-sm leading-relaxed text-muted">
            Every key provider authenticates with an API key. Only Ollama is
            keyless. The <C>Slug</C> is what you pass to{" "}
            <C>agentnode auth</C> and <C>llm.default_provider</C>.
          </p>
        </section>

        <section>
          <SectionHeading id="default-provider">Set the default provider</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            Choose which provider the host-side LLM binding uses by default, then
            confirm it is configured.
          </p>
          <CodeBlock title="terminal">{`$ agentnode config set llm.default_provider openai
$ agentnode auth status          # shows each provider and its effective source
$ agentnode auth test openai     # validate the key via a free endpoint`}</CodeBlock>
        </section>

        <section>
          <SectionHeading id="add-credentials">Add credentials</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            Store a key in the credential vault, or export the provider&apos;s
            environment variable (which overrides the vault):
          </p>
          <CodeBlock title="terminal">{`$ agentnode auth set deepseek    # prompts for the key (hidden input)
# or, per shell / CI:
$ export DEEPSEEK_API_KEY=sk-...`}</CodeBlock>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            Storage backends, env precedence, and the connector vault are covered
            in{" "}
            <Link href="/docs/credentials" className="text-primary hover:underline">
              Credentials &amp; Connectors
            </Link>
            .
          </p>
        </section>

        <section>
          <SectionHeading id="ollama">Use Ollama locally</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            Ollama is a keyless, local provider — no account and no per-token
            cost. Select it as the default, then check that your local Ollama is
            reachable.
          </p>
          <CodeBlock title="terminal">{`$ agentnode config set llm.default_provider ollama
$ agentnode auth test ollama   # localhost reachability check (not a key check)`}</CodeBlock>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            This path needs no hosted API key, but it is not zero-setup: you must
            install Ollama, have it running, and pull a model. AgentNode never
            installs or starts Ollama for you, and never probes it automatically.
          </p>
        </section>

        <section>
          <SectionHeading id="custom-endpoints">
            OpenAI-compatible and custom endpoints
          </SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            Any endpoint that implements the OpenAI chat-completions API can be
            added as a config entry under <C>llm.providers.&lt;name&gt;</C> — no
            code change per provider. Endpoints that deviate from that protocol
            are not guaranteed to work.
          </p>
          <CodeBlock title="~/.agentnode/config.json" language="json">{`{
  "llm": {
    "default_provider": "my-llm",
    "providers": {
      "my-llm": {
        "base_url": "https://my-host/v1",
        "api_key_env": "MY_LLM_API_KEY",
        "default_model": "my-model",
        "requires_key": true
      }
    }
  }
}`}</CodeBlock>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            A custom entry can also override a preset field (for example, a
            different <C>default_model</C>). Set <C>requires_key</C> to{" "}
            <C>false</C> for a local, keyless endpoint.
          </p>
        </section>

        <section>
          <SectionHeading id="runtime-selection">Providers vs the runtime</SectionHeading>
          <p className="text-sm leading-relaxed text-muted">
            This page covers <span className="text-foreground">which</span>{" "}
            providers exist and how their credentials are configured. How an
            agent or the tool loop actually <span className="text-foreground">calls</span>{" "}
            a model — registering tools, the auto tool-loop, and per-provider
            client wiring — lives in the{" "}
            <Link href="/docs/llm-runtime" className="text-primary hover:underline">
              LLM Runtime
            </Link>{" "}
            docs. Either way, keys stay host-side: a sandboxed agent reaches a
            model only through a host-side broker, so the provider key never
            enters the container.
          </p>
        </section>

        <section>
          <SectionHeading id="troubleshooting">Troubleshooting</SectionHeading>
          <DocTable
            headers={["Symptom", "Fix"]}
            rows={[
              ["Provider not configured", "Run agentnode auth status; set a key with agentnode auth set <provider>, or switch to Ollama."],
              ["agentnode auth test fails (401/403)", "The key was rejected. Re-set it with agentnode auth set <provider> and check it has the right scope/plan."],
              ["Ollama not reachable", "Start Ollama and pull a model; confirm with agentnode auth test ollama. AgentNode never starts it for you."],
              ["Wrong default provider", "Set it explicitly: agentnode config set llm.default_provider <name>."],
              ["Model unavailable", "The provider default may not exist on your plan; set a default_model under llm.providers.<name>."],
              ["Env var overrides a saved key", "Environment variables always win over the vault. Unset the variable, or check agentnode auth status for the effective source."],
            ]}
          />
        </section>

        <section>
          <SectionHeading id="related">Related</SectionHeading>
          <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              <Link href="/docs/credentials" className="text-primary hover:underline">
                Credentials &amp; Connectors
              </Link>{" "}
              — vault storage, env precedence, connector secrets.
            </li>
            <li>
              <Link href="/docs/llm-runtime" className="text-primary hover:underline">
                LLM Runtime
              </Link>{" "}
              — how agents call models (AgentNodeRuntime, meta-tools, tool loop).
            </li>
            <li>
              <Link href="/docs/quickstart" className="text-primary hover:underline">
                Quick Start
              </Link>{" "}
              — install, set up, and run your first capability.
            </li>
            <li>
              <Link href="/docs/sandbox" className="text-primary hover:underline">
                Execution Sandbox
              </Link>{" "}
              — how untrusted code is isolated from your keys.
            </li>
            <li>
              <Link href="/docs/agents" className="text-primary hover:underline">
                Agents
              </Link>{" "}
              — agent tiers and <C>llm_access</C>.
            </li>
          </ul>
        </section>
      </DocsShell>
    </>
  );
}
