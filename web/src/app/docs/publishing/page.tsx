import type { Metadata } from "next";
import Link from "next/link";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  CodeBlock,
  C,
} from "@/components/docs";

const TITLE = "Publishing Guide";
const DESCRIPTION = "Publish a skill to the AgentNode registry: project structure, manifest, local validation, the verification pipeline, and Gold-tier requirements.";
const PATH = "/docs/publishing";

export const metadata: Metadata = {
  title: "Publishing Guide — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Publishing Guide — Docs | AgentNode",
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
              Publishing a pack to AgentNode makes your AI tool discoverable,
              installable, and verifiable by any agent developer. This guide
              walks through the full process from account creation to published
              pack.
            </p>

            <SubHeading>Step 1: Create your publisher account</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Sign up at{" "}
              <Link
                href="/auth/register"
                className="text-primary hover:underline"
              >
                agentnode.net/auth/register
              </Link>{" "}
              and enable two-factor authentication. Your publisher namespace
              (e.g., <C>your-org</C>) appears in every package you publish and
              cannot be changed later.
            </p>

            <SubHeading>Step 2: Structure your project</SubHeading>
            <p className="mb-3 text-sm text-muted">
              A minimal pack has three files: the manifest, a pyproject.toml for
              Python packaging, and the tool module with your tool functions.
            </p>
            <CodeBlock title="project structure">{`my-pack/
  agentnode.yaml          # ANP manifest (required)
  pyproject.toml          # Python package config (required)
  src/
    my_pack/
      __init__.py
      tool.py             # Tool functions (required)`}</CodeBlock>

            <SubHeading>Step 3: Write your agentnode.yaml manifest</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The manifest is the source of truth for what your pack does, what
              it needs, and how it integrates. See the{" "}
              <a href="/docs/manifest" className="text-primary hover:underline">
                ANP Manifest Reference
              </a>{" "}
              below for every field.
            </p>
            <CodeBlock title="agentnode.yaml" language="yaml">{`manifest_version: "0.2"
package_id: "github-integration-pack"
package_type: "toolpack"
name: "GitHub Integration Pack"
publisher: "your-namespace"
version: "1.0.0"
summary: "Interact with GitHub repos, issues, and PRs."
description: "A comprehensive toolkit for GitHub automation including issue creation, PR review, repository management, and webhook handling."

runtime: "python"
entrypoint: "github_integration_pack.tool"
install_mode: "package"
hosting_type: "agentnode_hosted"

capabilities:
  tools:
    - name: "create_issue"
      capability_id: "github_integration"
      description: "Create a new GitHub issue"
      entrypoint: "github_integration_pack.tool:create_issue"
      input_schema:
        type: "object"
        properties:
          token:
            type: "string"
            description: "GitHub personal access token"
          repo:
            type: "string"
            description: "Repository in owner/repo format"
          title:
            type: "string"
          body:
            type: "string"
        required: ["token", "repo", "title"]
    - name: "list_repos"
      capability_id: "github_integration"
      description: "List repositories for authenticated user"
      entrypoint: "github_integration_pack.tool:list_repos"
      input_schema:
        type: "object"
        properties:
          token:
            type: "string"
        required: ["token"]

permissions:
  network:
    level: "unrestricted"
    justification: "Requires access to GitHub API"
  filesystem:
    level: "none"
  code_execution:
    level: "none"
  data_access:
    level: "input_only"

compatibility:
  frameworks: ["generic"]
  python: ">=3.10"

tags: ["github", "integration", "devtools", "automation"]`}</CodeBlock>

            <SubHeading>Step 4: Implement your tool functions</SubHeading>
            <CodeBlock title="src/github_integration_pack/tool.py" language="python">{`from agentnode_sdk.exceptions import AgentNodeToolError

def create_issue(inputs: dict) -> dict:
    """Create a new GitHub issue."""
    token = inputs["token"]
    repo = inputs["repo"]
    title = inputs["title"]
    body = inputs.get("body", "")

    # Your implementation here
    response = _github_api(token, f"/repos/{repo}/issues", {
        "title": title, "body": body
    })
    return {"issue_number": response["number"], "url": response["html_url"]}

def list_repos(inputs: dict) -> dict:
    """List repositories for authenticated user."""
    token = inputs["token"]
    repos = _github_api(token, "/user/repos")
    return {"repos": [{"name": r["name"], "url": r["html_url"]} for r in repos]}

# Optional: backward-compatible run() wrapper for v0.1 callers
# Not required for v0.2 — per-tool entrypoints (tool:create_issue, tool:list_repos) are used instead
def run(inputs: dict) -> dict:
    operation = inputs.get("operation", "list_repos")
    dispatch = {"create_issue": create_issue, "list_repos": list_repos}
    handler = dispatch.get(operation)
    if not handler:
        raise AgentNodeToolError(f"Unknown operation: {operation}", tool_name=operation)
    return handler(inputs)`}</CodeBlock>

            <SubHeading>Step 5: Validate and verify locally</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Run the local verification pipeline to confirm your package will reach Gold tier
              on first publish. This simulates the exact same checks the server runs.
            </p>
            <CodeBlock title="terminal">{`$ agentnode validate .

Validating github-integration-pack@1.0.0
  [PASS] Manifest syntax valid
  [PASS] Required fields present
  [PASS] Verification cases defined (2 cases)
  [PASS] Cassette files exist

  Max tier              Gold
  Mode                  fixture
  Cases                 2`}</CodeBlock>
            <p className="my-3 text-sm text-muted">
              For API connectors that make external HTTP calls, record VCR cassettes first:
            </p>
            <CodeBlock title="terminal">{`$ agentnode record-cases .

Recording cassettes for github-integration-pack
  [OK] create_issue -> fixtures/cassettes/create_issue.yaml
  [OK] list_repos -> fixtures/cassettes/list_repos.yaml

  Cassette Warnings
  [DYNAMIC] Fields that may change between runs:
    - interactions[0].response.headers.Date

  Next: agentnode verify-local .`}</CodeBlock>
            <p className="my-3 text-sm text-muted">
              Then run the full verification pipeline locally:
            </p>
            <CodeBlock title="terminal">{`$ agentnode verify-local .

Verifying github-integration-pack@1.0.0

  Pipeline
  [PASS] Install
  [PASS] Import
  [PASS] Smoke
  [PASS] Tests      2 passed in 0.3s
  [PASS] Contract
  [PASS] Reliability  100.0%
  [PASS] Determinism  100.0%

  Score                 95/95
  Tier                  Gold
  Mode                  fixture

  This package will reach Gold tier after publishing.`}</CodeBlock>

            <SubHeading>Step 6: Publish</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Set your API key (from your{" "}
              <Link href="/dashboard" className="text-primary hover:underline">
                dashboard
              </Link>
              ), then publish:
            </p>
            <CodeBlock title="terminal">{`$ export AGENTNODE_API_KEY=ank_your_key_here
$ agentnode publish .

  AgentNode Publish
  Package    github-integration-pack@1.0.0
  Type       toolpack

  Validation    8 checks passed
  Artifact      14.2 KB, 6 files

  Publishing to api.agentnode.net...
  Published github-integration-pack@1.0.0

  https://agentnode.net/packages/github-integration-pack`}</CodeBlock>
            <p className="mt-3 text-sm text-muted">
              Use <C>--dry-run</C> to preview without uploading,{" "}
              <C>--skip-validate</C> to continue past validation warnings, or{" "}
              <C>--token &lt;key&gt;</C> to pass the API key directly.
            </p>

            <div className="mt-6 rounded-lg border border-primary/20 bg-primary/5 p-4">
              <p className="text-sm text-muted">
                <span className="font-medium text-foreground">
                  Tip:
                </span>{" "}
                Packages that pass <C>agentnode verify-local</C> reach Gold tier on their
                first publish attempt. No debugging in the blind. The recommended flow is:{" "}
                <C>init</C> → <C>validate</C> → <C>record-cases</C> → <C>verify-local</C> → publish.
              </p>
            </div>
          </section>
      </DocsShell>
    </>
  );
}
