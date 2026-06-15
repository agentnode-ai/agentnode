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

const TITLE = "Agents";
const DESCRIPTION =
  "How AgentNode agents run: trust-gated execution, the manifest, llm_access, the host-side broker, and the opt-in agent sandbox (off by default).";
const PATH = "/docs/agents";

export const metadata: Metadata = {
  title: "Agents — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Agents — Docs | AgentNode",
    description: DESCRIPTION,
    type: "website",
    url: PATH,
    siteName: "AgentNode",
  },
};

const TIER_ROWS = [
  ["llm_only", "Pure LLM reasoning — no tools, no API calls", "Blog writer, report generator"],
  ["llm_plus_tools", "LLM plus AgentNode tool packs (search, extract, analyze)", "Deep research, code review"],
  ["llm_plus_credentials", "LLM plus tools plus external API credentials", "CRM enrichment, cloud cost analysis"],
];

const EXEC_ROWS = [
  ["curated", "Host (thread or process)", "No (host execution)", "Bound LLM client (host key)", "AgentNode-reviewed"],
  ["trusted", "Host (thread or process)", "No (host execution)", "Bound LLM client (host key)", "Vetted third-party"],
  ["verified", "Refused by default; container if agent sandbox is ON", "Yes, when enabled", "Host-side broker only", "Community"],
  ["unverified", "Refused by default; container if agent sandbox is ON", "Yes, when enabled", "Host-side broker only", "Community"],
  ["unknown / missing", "Refused by default; container if agent sandbox is ON", "Yes, when enabled", "Host-side broker only", "Treated as community"],
];

const MANIFEST_ROWS = [
  ["entrypoint", "Yes*", "Python module:function that runs the agent (*not needed for sequential orchestration)."],
  ["goal", "Yes", "What the agent does (shown in the UI; overridable at run time)."],
  ["system_prompt", "Recommended", "Behavior description; shown as 'Agent Behavior' on the package page."],
  ["tier", "No", "llm_only | llm_plus_tools | llm_plus_credentials."],
  ["llm.required", "No", "Whether the agent needs an LLM (llm_only implies true)."],
  ["tool_access.allowed_packages", "No", "Tool packs the agent may call. Omit/null = full registry; [] = no tools."],
  ["limits.max_iterations", "No", "Reasoning iterations before stopping (default 12)."],
  ["limits.max_tool_calls", "No", "Tool calls before stopping (default 40)."],
  ["limits.max_runtime_seconds", "No", "Wall-clock budget (default 180)."],
  ["isolation", "No", "thread (default) or process — host isolation, not a security sandbox."],
];

export default function Page() {
  return (
    <>
      <DocsJsonLd title={TITLE} description={DESCRIPTION} path={PATH} />
      <DocsShell title={TITLE}>
        <section>
          <p className="text-sm leading-relaxed text-muted">
            AgentNode agents are installable packages that declare their goal,
            behavior, tools, and limits in a manifest. How an agent runs depends
            on trust: by default only <C>trusted</C> and <C>curated</C> agents
            execute — on the host — while community agents are refused unless you
            opt into the agent sandbox. LLM access is always mediated host-side.
          </p>
        </section>

        <section>
          <SectionHeading id="what-is-an-agent">What is an AgentNode agent?</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            An agent is a package whose <C>agent:</C> manifest section declares a
            goal, optional system prompt, an LLM requirement, allowed tools, and
            run limits. Unlike a tool pack (a single capability) or an MCP server
            (an external tool process), an agent orchestrates an LLM and tools
            toward a goal, and runs through its own agent runner.
          </p>
          <p className="text-sm leading-relaxed text-muted">
            Agents are installed and inspected like any other package. They are
            not autonomous by magic: every run is bounded by declared limits,
            trust policy, and Guard.
          </p>
        </section>

        <section>
          <SectionHeading id="capability-tiers">Capability tiers</SectionHeading>
          <p className="mb-4 text-sm leading-relaxed text-muted">
            The <C>tier</C> describes what an agent needs. It is independent of
            the trust level, which decides how the agent is executed (next
            section).
          </p>
          <DocTable headers={["Tier", "Needs", "Example"]} rows={TIER_ROWS} />
        </section>

        <section>
          <SectionHeading id="execution-and-trust">Execution &amp; trust</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            An agent&apos;s own orchestration code runs on the{" "}
            <span className="text-foreground">host</span> (a thread by default,
            or a child process), not inside the container sandbox. Because of
            that, the trust gate is what keeps untrusted agent code off your
            machine:
          </p>
          <DocTable
            headers={["Trust", "Default execution", "Sandboxed?", "LLM key", "Notes"]}
            rows={EXEC_ROWS}
          />
          <ul className="mt-4 list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              <span className="text-foreground">Trusted / curated agents run on
              the host</span> with full host environment access and a bound LLM
              client. They are policy-checked, not OS-sandboxed.
            </li>
            <li>
              <span className="text-foreground">Community agents (verified /
              unverified / unknown) are refused by default</span> — execution
              requires trust ≥ trusted. They run only if you opt into the agent
              sandbox, and then sandbox-or-fail-closed (never on the host).
            </li>
            <li>
              An agent&apos;s <span className="text-foreground">tool calls</span>{" "}
              re-enter the normal <C>run_tool</C> gate, so each tool is subject
              to its own trust, Guard, and sandbox rules — independent of the
              agent.
            </li>
          </ul>
        </section>

        <section>
          <SectionHeading id="agent-sandbox">Agent sandbox</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            The agent sandbox is{" "}
            <span className="text-foreground">off by default</span>. Enable it to
            let community agents run, isolated, instead of being refused:
          </p>
          <CodeBlock title="terminal">{`$ agentnode config set agent_sandbox.enabled true
# or, per shell:  export AGENTNODE_AGENT_SANDBOX=1`}</CodeBlock>
          <ul className="mt-4 list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              When enabled, verified and unverified agents run
              sandbox-or-fail-closed in the hardened container — if no runtime or
              image is available, they are blocked, never downgraded to the host.
            </li>
            <li>
              LLM calls go through a{" "}
              <span className="text-foreground">host-side broker</span>: the
              provider key stays on the host and never enters the container,
              audit records, manifests, or lockfiles.
            </li>
            <li>
              Trusted and curated agents are unaffected — they continue to run on
              the host.
            </li>
          </ul>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            Container hardening and the fail-closed model are documented in{" "}
            <Link href="/docs/sandbox" className="text-primary hover:underline">
              Execution Sandbox
            </Link>
            .
          </p>
        </section>

        <section>
          <SectionHeading id="llm-access">llm_access</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            <C>llm_access</C> governs whether a{" "}
            <span className="text-foreground">sandboxed community agent</span>{" "}
            may borrow the host&apos;s LLM through the broker. It is default-deny
            and does not apply to trusted/curated host agents, which use a bound
            client directly.
          </p>
          <DocTable
            headers={["Field", "Effect"]}
            rows={[
              ["enabled", "Must be true for the agent to reach the host LLM. Default-deny."],
              ["max_calls", "Cap on broker calls per run (built-in ceiling 20)."],
              ["max_input_chars", "Cap on input characters per call (built-in ceiling 24000)."],
              ["max_output_chars", "Cap on output characters per call (built-in ceiling 24000)."],
              ["allowed_models", "Omit = host picks the model; a list = only those models; [] = refuse all."],
            ]}
          />
          <p className="mt-3 text-sm leading-relaxed text-muted">
            The host configuration ceiling (<C>agent_sandbox.llm</C>) always
            wins: it can lower any cap or force-disable access, and a higher
            manifest value is clamped to it. Refusals come back as structured
            per-call errors, never a host fallback.
          </p>
        </section>

        <section>
          <SectionHeading id="tools-and-policies">Tools &amp; policies</SectionHeading>
          <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              An agent may call only the packs in{" "}
              <C>tool_access.allowed_packages</C>. Omit or null means the full
              registry; an empty list means no tools (e.g. an <C>llm_only</C>{" "}
              agent).
            </li>
            <li>
              Every tool call re-enters <C>run_tool</C>, so trust gating, Guard
              action policy, input inspection, rate limits, and (for community
              tools) the sandbox all apply per call.
            </li>
            <li>
              The loop is bounded by <C>limits</C> — iterations, tool calls, and
              wall-clock seconds — so a run always terminates.
            </li>
          </ul>
        </section>

        <section>
          <SectionHeading id="manifest">The agent manifest</SectionHeading>
          <p className="mb-4 text-sm leading-relaxed text-muted">
            All fields below live under the <C>agent:</C> section and are visible
            on the package detail page.
          </p>
          <DocTable headers={["Field", "Required", "Description"]} rows={MANIFEST_ROWS} />
          <CodeBlock title="agentnode.yaml (agent section)" language="yaml">{`agent:
  entrypoint: my_agent.run:run        # module:function
  tier: llm_plus_tools                # llm_only | llm_plus_tools | llm_plus_credentials
  goal: Summarize a PDF and extract action items
  system_prompt: You are a careful research assistant.
  llm:
    required: true
  tool_access:
    allowed_packages:                 # omit/null = full registry; [] = no tools
      - pdf-reader-pack
  limits:
    max_iterations: 12
    max_tool_calls: 40
    max_runtime_seconds: 180
  isolation: thread                   # thread (default) | process`}</CodeBlock>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            Sandboxed community agents additionally read an <C>llm_access</C>{" "}
            block (above). Optional <C>termination</C>, <C>error_handling.retry</C>,
            and <C>orchestration</C> blocks tune stopping and retries.
          </p>
        </section>

        <section>
          <SectionHeading id="install-run-inspect">Install, inspect &amp; run</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            Agents use the same commands as any package. Inspect before running
            to see trust, permissions, and the policy preview.
          </p>
          <CodeBlock title="terminal">{`$ agentnode install deep-research-agent
$ agentnode inspect deep-research-agent   # trust, permissions, risk, policy preview`}</CodeBlock>
          <CodeBlock title="run_agent.py" language="python">{`from agentnode_sdk import run_tool

result = run_tool(
    "deep-research-agent",
    goal="Compare React vs Vue adoption in 2026",
)
print(result.result)`}</CodeBlock>
        </section>

        <section>
          <SectionHeading id="honest-limits">Honest limits</SectionHeading>
          <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>Not every agent is sandboxed. The agent sandbox is off by default.</li>
            <li>
              Trusted and curated agents run on the host with full environment
              access — policy-checked, not OS-sandboxed.
            </li>
            <li>
              The sandbox does not grant arbitrary internet or secrets; it
              isolates, it does not entitle.
            </li>
            <li>
              <C>llm_access</C>, limits, and Guard constrain behavior — they do
              not make an agent &quot;safe&quot; by themselves. Review the agent
              and its trust level too.
            </li>
            <li>
              When an agent uses a hosted model, that provider still receives the
              prompts and returns the responses for those calls.
            </li>
          </ul>
        </section>

        <section>
          <SectionHeading id="troubleshooting">Troubleshooting</SectionHeading>
          <DocTable
            headers={["Symptom", "Fix"]}
            rows={[
              ["Agent refused (trust below trusted)", "Community agents are refused by default. Use a trusted/curated agent, or enable the agent sandbox to run it isolated."],
              ["Model not allowed", "The host ceiling or the agent's allowed_models excluded the model. Adjust agent_sandbox.llm or the manifest."],
              ["No LLM provider found", "Set a key (agentnode auth set <provider>) or export OPENAI_API_KEY / ANTHROPIC_API_KEY. See LLM Providers."],
              ["Sandbox unavailable", "With the agent sandbox on, a missing runtime/image blocks the run (fail-closed). Run agentnode sandbox doctor."],
              ["Tool access denied", "The tool is outside allowed_packages, or its own trust/Guard policy blocked it. Run agentnode inspect <slug>."],
            ]}
          />
        </section>

        <section>
          <SectionHeading id="related">Related</SectionHeading>
          <ul className="list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              <Link href="/docs/sandbox" className="text-primary hover:underline">
                Execution Sandbox
              </Link>{" "}
              — container hardening and the fail-closed model.
            </li>
            <li>
              <Link href="/docs/security" className="text-primary hover:underline">
                Security Model
              </Link>{" "}
              — what is enforced per trust tier.
            </li>
            <li>
              <Link href="/docs/llm-runtime" className="text-primary hover:underline">
                LLM Runtime
              </Link>{" "}
              — how LLM agents register tools and run the tool loop.
            </li>
            <li>
              <Link href="/docs/llm-providers" className="text-primary hover:underline">
                LLM Providers
              </Link>{" "}
              — providers and the host-side binding.
            </li>
            <li>
              <Link href="/docs/credentials" className="text-primary hover:underline">
                Credentials &amp; Connectors
              </Link>{" "}
              — where keys live and how the broker uses them.
            </li>
            <li>
              <Link href="/docs/guard" className="text-primary hover:underline">
                AgentNode Guard
              </Link>{" "}
              — per-action policy applied to tool calls.
            </li>
            <li>
              <Link href="/search" className="text-primary hover:underline">
                Browse the registry
              </Link>{" "}
              — find agents to install.
            </li>
          </ul>
        </section>
      </DocsShell>
    </>
  );
}
