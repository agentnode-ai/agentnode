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

const TITLE = "Publishing Credentialed Tool Packs";
const DESCRIPTION =
  "How to publish a tool pack that needs a user-provided API key: declare env_requirements and the network egress allowlist (permissions.network.allowed_domains). Covers what each field means, the fail-closed rules, what the user sees at install and run time, and how keys reach sandboxed code by name only — AgentNode never stores them.";
const PATH = "/docs/credentialed-toolpacks";

export const metadata: Metadata = {
  title: "Publishing Credentialed Tool Packs — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Publishing Credentialed Tool Packs — Docs | AgentNode",
    description: DESCRIPTION,
    type: "website",
    url: PATH,
    siteName: "AgentNode",
  },
};

const ENV_FIELD_HEADERS = ["Field", "Type", "Required", "Meaning"];
const ENV_FIELD_ROWS = [
  ["name", "string", "Yes", "The environment variable name, e.g. AHREFS_API_KEY. Case-sensitive. Never a value."],
  ["required", "boolean", "No (default true)", "If true, a run is blocked until the variable is set. If false, the tool runs without it (the key is passed through only when present)."],
  ["description", "string", "No", "A short human explanation shown on the package page and in the publish form, e.g. \"Ahrefs API key\"."],
];

const FLOW_HEADERS = ["Step", "What happens"];
const FLOW_ROWS = [
  ["install", "The declared variables are listed with their set / not-set status. Required-but-unset ones are called out."],
  ["run (missing required key)", "The run is refused before dispatch with a clear message naming the variable — no cryptic tool error."],
  ["run (community pack, sandboxed)", "One consent prompt names the keys and the exact domains. The container runs on an egress proxy limited to those domains; the key is passed by name only (never on argv, in the process spec, or in logs)."],
  ["run (curated / trusted, host)", "The tool reads the key from the environment directly, as any host process would."],
];

export default function Page() {
  return (
    <>
      <DocsJsonLd title={TITLE} description={DESCRIPTION} path={PATH} />
      <DocsShell title={TITLE}>
        <section>
          <p className="text-sm leading-relaxed text-muted">
            A tool pack often needs an API key that belongs to the user — an
            Ahrefs key, an OpenAI key, a private service token. AgentNode is{" "}
            <strong className="text-foreground">bring-your-own-key</strong>: the
            user supplies the key in their own environment, and AgentNode never
            stores, sees, or brokers it. As a publisher you do two things:{" "}
            <em>declare</em> which variables your pack needs, and <em>declare</em>{" "}
            the exact domains it is allowed to reach. Those two declarations are
            what make the key flow safe and, for community packs, what make it
            work at all.
          </p>
        </section>

        <section>
          <SectionHeading id="declare-env">1. Declare the credentials</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            Add an <C>env_requirements</C> list to your manifest. Each entry
            names one environment variable — <strong className="text-foreground">names
            only, never values</strong>.
          </p>
          <CodeBlock title="agentnode.yaml">{`env_requirements:
  - name: "AHREFS_API_KEY"
    required: true
    description: "Ahrefs API key"
  - name: "AHREFS_WORKSPACE"
    required: false
    description: "Optional workspace id"`}</CodeBlock>
          <DocTable headers={ENV_FIELD_HEADERS} rows={ENV_FIELD_ROWS} />
          <p className="mt-3 text-sm leading-relaxed text-muted">
            Your tool code reads the value the ordinary way — for example{" "}
            <C>os.environ[&quot;AHREFS_API_KEY&quot;]</C>. The declaration does
            not inject anything; it tells AgentNode which names to check, prompt
            for, and pass through.
          </p>
        </section>

        <section>
          <SectionHeading id="declare-domains">2. Declare the egress allowlist</SectionHeading>
          <p className="mb-3 text-sm leading-relaxed text-muted">
            A pack that carries a secret must also say exactly where that secret
            may go. Set <C>permissions.network.level</C> to <C>restricted</C> and
            list the API hosts in <C>allowed_domains</C>. For a community pack
            this is <strong className="text-foreground">mandatory</strong>: a
            credentialed run with no valid allowlist is refused — a secret never
            rides an open or unrestricted network.
          </p>
          <CodeBlock title="agentnode.yaml">{`permissions:
  network:
    level: "restricted"          # required for credentialed community packs
    allowed_domains:
      - "api.ahrefs.com"         # the only host the key can reach
  filesystem:
    level: "none"
  code_execution:
    level: "none"`}</CodeBlock>
          <p className="mt-3 text-sm leading-relaxed text-muted">
            List every host your tool actually calls, and nothing more. When the
            pack runs sandboxed, AgentNode starts an egress proxy limited to
            exactly these domains; a request to anywhere else is dropped. This is
            the mechanism that prevents a compromised or malicious pack from
            exfiltrating the user&apos;s key.
          </p>
        </section>

        <section>
          <SectionHeading id="what-user-sees">What the user experiences</SectionHeading>
          <DocTable headers={FLOW_HEADERS} rows={FLOW_ROWS} />
          <p className="mt-3 text-sm leading-relaxed text-muted">
            The consent a user grants is bound to your exact package identity —
            slug, version, artifact hash, the declared key names, and the
            declared domains. If you publish a new version, change the key set,
            or change the domains, the user is asked again. Consent can never
            silently carry over to a different pack or a widened set of
            permissions.
          </p>
        </section>

        <section>
          <SectionHeading id="rules">Fail-closed rules to keep in mind</SectionHeading>
          <ul className="ml-4 list-disc space-y-2 text-sm leading-relaxed text-muted">
            <li>
              A credentialed community pack with an empty or invalid{" "}
              <C>allowed_domains</C> is <strong className="text-foreground">refused</strong>,
              not run without protection.
            </li>
            <li>
              A missing <C>required</C> variable blocks the run up front with a
              message that names the variable.
            </li>
            <li>
              Keys are passed to the container by name only. The value is read by
              the container runtime, never placed on the command line, in the
              process spec, or in any log.
            </li>
            <li>
              <C>env_requirements</C> and <C>allowed_domains</C> are sealed into
              the user&apos;s lockfile at install. Tampering with either breaks
              lockfile integrity.
            </li>
            <li>
              Non-credentialed packs are unaffected — declaring no{" "}
              <C>env_requirements</C> keeps the ordinary run path.
            </li>
          </ul>
        </section>

        <section>
          <SectionHeading id="checklist">Publisher checklist</SectionHeading>
          <ol className="ml-4 list-decimal space-y-2 text-sm leading-relaxed text-muted">
            <li>Read the key in your tool code from the environment.</li>
            <li>Declare each variable under <C>env_requirements</C> (name, required, description).</li>
            <li>Set <C>network.level: restricted</C> and list every API host under <C>allowed_domains</C>.</li>
            <li>
              Validate before publishing:{" "}
              <C>agentnode validate .</C> — see the{" "}
              <Link href="/docs/publishing" className="text-primary hover:underline">
                Publishing Guide
              </Link>{" "}
              and the{" "}
              <Link href="/docs/manifest" className="text-primary hover:underline">
                ANP Manifest Reference
              </Link>
              .
            </li>
          </ol>
          <p className="mt-4 text-sm leading-relaxed text-muted">
            For how AgentNode isolates execution per trust tier, see the{" "}
            <Link href="/docs/sandbox" className="text-primary hover:underline">
              Sandbox
            </Link>{" "}
            docs. For where a user&apos;s keys are stored, see{" "}
            <Link href="/docs/credentials" className="text-primary hover:underline">
              Credentials &amp; Connectors
            </Link>
            .
          </p>
        </section>
      </DocsShell>
    </>
  );
}
