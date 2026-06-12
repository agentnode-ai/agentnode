import type { Metadata } from "next";
import Link from "next/link";
import { DocsShell, DocsJsonLd, C, CodeBlock } from "@/components/docs";

const TITLE = "LLM Providers";
const DESCRIPTION =
  "Connect AgentNode to OpenAI, Anthropic, OpenRouter, DeepSeek, Mistral, Qwen, Gemini or local Ollama — any OpenAI-compatible endpoint works.";
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

export default function Page() {
  return (
    <>
      <DocsJsonLd title={TITLE} description={DESCRIPTION} path={PATH} />
      <DocsShell title={TITLE}>
        <section>
          <p className="mb-4 text-sm leading-relaxed text-muted">
            Since SDK 0.15, the LLM runtime binds{" "}
            <strong className="text-foreground">
              any endpoint that speaks the OpenAI-compatible protocol
            </strong>
            . Built-in presets cover OpenAI, Anthropic, OpenRouter, DeepSeek,
            Mistral, Qwen, Gemini — and{" "}
            <strong className="text-foreground">Ollama as a keyless local provider</strong>{" "}
            (no account, no per-token cost, opt-in only). Custom endpoints are
            plain config entries under <C>llm.providers.&lt;name&gt;</C>; no
            code changes per provider.
          </p>

          <p className="mb-4 text-sm leading-relaxed text-muted">
            Keys are stored per provider via the credential vault and can also
            come from the provider&apos;s environment variable (environment
            always wins):
          </p>
          <CodeBlock title="terminal">{`$ agentnode auth set deepseek              # store a key in the OS keychain
$ agentnode auth test deepseek             # validate it (free endpoint, no completion)
$ agentnode config set llm.default_provider ollama   # go keyless + local`}</CodeBlock>

          <div className="mt-4 rounded-lg border border-primary/20 bg-primary/5 p-4">
            <p className="text-sm text-muted">
              The full provider guide (preset table with environment variables
              and default models, custom endpoint configuration, sandboxed-agent
              broker behavior) is being written. Until then,{" "}
              <C>agentnode auth status</C> lists every provider with its
              effective source, and the{" "}
              <Link href="/docs/llm-runtime" className="text-primary hover:underline">
                LLM Runtime
              </Link>{" "}
              page covers connecting agents to the registry.
            </p>
          </div>
        </section>
      </DocsShell>
    </>
  );
}
