import type { Metadata } from "next";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "GitHub Action";
const DESCRIPTION = "Automate AgentNode publishing from CI: the agentnode/publish action with dry-run support, release triggers, and API-key configuration.";
const PATH = "/docs/github-action";

export const metadata: Metadata = {
  title: "GitHub Action — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "GitHub Action — Docs | AgentNode",
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
              The <C>agentnode/publish@v1</C> GitHub Action automates pack
              publishing from your CI/CD pipeline. Push a tag or create a
              release, and the action validates, scans, signs, and publishes
              your pack automatically.
            </p>

            <SubHeading>Basic workflow</SubHeading>
            <CodeBlock title=".github/workflows/publish.yml" language="yaml">{`name: Publish to AgentNode
on:
  release:
    types: [published]

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Publish pack
        uses: agentnode/publish@v1
        with:
          api-key: \${{ secrets.AGENTNODE_API_KEY }}`}</CodeBlock>

            <SubHeading>Action inputs</SubHeading>
            <DocTable
              headers={["Input", "Required", "Default", "Description"]}
              rows={[
                ["api-key", "Yes", "--", "Your AgentNode API key. Store as a repository secret."],
                ["dry-run", "No", "false", "Run validation and scanning without publishing. Set to \"true\" for PR checks."],
                ["directory", "No", ".", "Path to the pack directory containing agentnode.yaml."],
              ]}
            />

            <SubHeading>Dry-run on pull requests</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Use dry-run mode to validate manifests on every pull request
              without publishing:
            </p>
            <CodeBlock title=".github/workflows/validate.yml" language="yaml">{`name: Validate AgentNode Pack
on:
  pull_request:
    paths:
      - "agentnode.yaml"
      - "src/**"

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Validate pack
        uses: agentnode/publish@v1
        with:
          api-key: \${{ secrets.AGENTNODE_API_KEY }}
          dry-run: true`}</CodeBlock>

            <SubHeading>Example output</SubHeading>
            <CodeBlock title="github actions log">{`Run agentnode/publish@v1
  Validating agentnode.yaml...
    Manifest syntax       OK
    Capability IDs        OK (1 tool, 0 resources)
    Permissions           OK (network: unrestricted)
    Entrypoint            OK (github_integration_pack.tool)
    Compatibility         OK (3 frameworks)

  Running security scan...
    Bandit scan           passed (0 issues)
    Dependency audit      passed

  Publishing github-integration-pack@1.0.0...
    Uploading package     done
    Signing package       done (Ed25519)
    Indexing capabilities done

  Published: https://agentnode.net/packages/github-integration-pack`}</CodeBlock>

            <SubHeading>Triggering on tags</SubHeading>
            <p className="mb-3 text-sm text-muted">
              If you prefer tag-based releases instead of GitHub Releases:
            </p>
            <CodeBlock title=".github/workflows/publish-on-tag.yml" language="yaml">{`name: Publish to AgentNode
on:
  push:
    tags:
      - "v*"

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Publish pack
        uses: agentnode/publish@v1
        with:
          api-key: \${{ secrets.AGENTNODE_API_KEY }}`}</CodeBlock>
          </section>
      </DocsShell>
    </>
  );
}
