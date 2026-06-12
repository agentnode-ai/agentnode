import type { Metadata } from "next";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  CodeBlock,
  C,
} from "@/components/docs";

const TITLE = "Python SDK";
const DESCRIPTION = "AgentNode Python SDK reference: search, resolve, install, run tools, gap detection, smart_run, auto-upgrade policies, and package metadata.";
const PATH = "/docs/python-sdk";

export const metadata: Metadata = {
  title: "Python SDK — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Python SDK — Docs | AgentNode",
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
              The Python SDK provides programmatic access to the AgentNode
              registry for search, resolution, trust checking, installation,
              tool loading, and capability gap detection. Use it to build
              agents that detect missing capabilities and safely acquire
              verified skills on demand.
            </p>

            <SubHeading>Installation</SubHeading>
            <CodeBlock title="terminal">{`$ pip install agentnode-sdk`}</CodeBlock>

            <SubHeading>Initialization</SubHeading>
            <CodeBlock title="app.py" language="python">{`from agentnode_sdk import AgentNodeClient

# API key authentication (recommended)
client = AgentNodeClient(api_key="ank_live_abc123def456")

# Or use a bearer token
client = AgentNodeClient(token="your_bearer_token")

# Custom base URL (for self-hosted registries)
client = AgentNodeClient(
    api_key="ank_live_abc123def456",
    base_url="https://api.your-registry.com/v1"
)`}</CodeBlock>

            <SubHeading>Search</SubHeading>
            <CodeBlock title="search.py" language="python">{`result = client.search(
    query="pdf extraction",
    framework="langchain",
    per_page=10
)

print(f"Found {result.total} packages")
for hit in result.hits:
    print(f"  {hit.slug}  {hit.trust_level}  {hit.summary}")`}</CodeBlock>

            <SubHeading>Resolve</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Resolve finds the best package for a set of capability IDs,
              scoring each candidate on capability match, framework fit,
              runtime compatibility, trust level, and permissions.
            </p>
            <CodeBlock title="resolve.py" language="python">{`result = client.resolve(
    capabilities=["pdf_extraction", "web_search"],
    framework="langchain"
)

for match in result.results:
    print(f"{match.slug} v{match.version}")
    print(f"  Score: {match.score}  Trust: {match.trust_level}")
    print(f"  Breakdown: cap={match.breakdown.capability} "
          f"fw={match.breakdown.framework} "
          f"trust={match.breakdown.trust}")`}</CodeBlock>

            <SubHeading>Pre-flight check</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Check whether a package can be installed under given trust and
              permission constraints — without downloading anything.
            </p>
            <CodeBlock title="check.py" language="python">{`check = client.can_install(
    "pdf-reader-pack",
    require_trusted=True,
    denied_permissions=["network", "code_execution"]
)

if check.allowed:
    print(f"OK — trust: {check.trust_level}")
else:
    print(f"Blocked: {check.reason}")`}</CodeBlock>

            <SubHeading>Install</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Downloads the artifact, verifies the SHA-256 hash, extracts to{" "}
              <C>~/.agentnode/packages/</C>, installs pip dependencies, and
              writes <C>agentnode.lock</C>.
            </p>
            <CodeBlock title="install.py" language="python">{`result = client.install("pdf-reader-pack", require_trusted=True)

print(result.message)       # "Installed pdf-reader-pack@1.2.0"
print(result.installed)     # True
print(result.hash_verified) # True`}</CodeBlock>

            <SubHeading>Load and run tools</SubHeading>
            <CodeBlock title="run.py" language="python">{`# Load a tool from an installed package
extract = client.load_tool("pdf-reader-pack")
result = extract({"file_path": "report.pdf"})

# Multi-tool packs: load a specific tool by name
describe = client.load_tool("csv-analyzer-pack", tool_name="describe")
summary = describe({"file_path": "data.csv"})`}</CodeBlock>

            <SubHeading>One-call autonomous install</SubHeading>
            <p className="mb-3 text-sm text-muted">
              For agents that need to self-upgrade: describe what you need and
              let AgentNode handle the rest.
            </p>
            <CodeBlock title="autonomous.py" language="python">{`# Resolve + trust check + install in one call
result = client.resolve_and_install(
    capabilities=["pdf_extraction"],
    require_trusted=True  # only install trusted/curated packages
)

if result.installed:
    tool = client.load_tool(result.slug)
    data = tool({"file_path": "report.pdf"})
else:
    print(f"Could not install: {result.message}")`}</CodeBlock>

            <SubHeading>Capability gap detection (v0.4.0)</SubHeading>
            <p className="mb-3 text-sm text-muted">
              AgentNode can analyze runtime errors to detect missing capabilities
              — without any LLM. Three detection layers with confidence levels:
            </p>
            <ul className="mb-4 list-disc pl-5 text-sm text-muted space-y-1">
              <li><strong>High</strong> — <C>ImportError</C> for a known module (e.g. pdfplumber, pandas, selenium)</li>
              <li><strong>Medium</strong> — Error message contains technical keywords (e.g. &quot;chromedriver&quot;, &quot;csv parser&quot;)</li>
              <li><strong>Low</strong> — Context hints like file extensions or URLs</li>
            </ul>
            <CodeBlock title="detect.py" language="python">{`from agentnode_sdk import detect_gap

gap = detect_gap(ImportError("No module named 'pdfplumber'"))
print(gap.capability)   # "pdf_extraction"
print(gap.confidence)   # "high"
print(gap.source)       # "import_error"

# Context helps when the error itself isn't specific
gap = detect_gap(RuntimeError("failed"), context={"file": "report.pdf"})
print(gap.capability)   # "pdf_extraction"
print(gap.confidence)   # "low"`}</CodeBlock>

            <SubHeading>detect_and_install() (v0.4.0)</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The product-level API for self-upgrading agents. Detects the gap,
              resolves the best match, and installs it — all in one call.
            </p>
            <CodeBlock title="detect_install.py" language="python">{`try:
    result = my_agent_logic()
except Exception as exc:
    upgrade = client.detect_and_install(
        exc,
        auto_upgrade_policy="safe",  # only verified+ skills
        on_detect=lambda cap, conf, err: print(f"Detected: {cap} ({conf})"),
        on_install=lambda slug: print(f"Installed: {slug}"),
    )

    if upgrade.installed:
        result = my_agent_logic()  # retry manually
    else:
        print(f"Detection: {upgrade.capability} ({upgrade.confidence})")
        print(f"Error: {upgrade.error}")`}</CodeBlock>

            <SubHeading>smart_run() (v0.4.0)</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Convenience wrapper: wrap your logic and let AgentNode handle
              detection, installation, and exactly one retry automatically.
            </p>
            <CodeBlock title="smart.py" language="python">{`result = client.smart_run(
    lambda: process_pdf("report.pdf"),
    auto_upgrade_policy="safe",
)

if result.success:
    print(result.result)          # your function's return value
    print(result.upgraded)        # True if a skill was installed
    print(result.installed_slug)  # e.g. "pdf-reader-pack"
    print(result.duration_ms)     # total time including retry
else:
    print(result.error)
    print(result.original_error)  # the first error, always available`}</CodeBlock>

            <SubHeading>Auto-upgrade policies (v0.4.0)</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Named policies control what gets auto-installed. When set, the policy
              overrides individual parameters like <C>require_verified</C>.
            </p>
            <div className="mb-4 overflow-hidden rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-card">
                    <th className="px-4 py-2 text-left font-medium text-foreground">Policy</th>
                    <th className="px-4 py-2 text-left font-medium text-foreground">Behavior</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-border">
                    <td className="px-4 py-2 font-mono text-xs text-foreground">&quot;off&quot;</td>
                    <td className="px-4 py-2 text-muted">Detect only, never install</td>
                  </tr>
                  <tr className="border-b border-border">
                    <td className="px-4 py-2 font-mono text-xs text-primary">&quot;safe&quot;</td>
                    <td className="px-4 py-2 text-muted">Auto-install verified+ skills (recommended)</td>
                  </tr>
                  <tr>
                    <td className="px-4 py-2 font-mono text-xs text-foreground">&quot;strict&quot;</td>
                    <td className="px-4 py-2 text-muted">Auto-install trusted+ skills only</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="mb-3 text-sm text-muted">
              Low-confidence detections are blocked from auto-install by default.
              Use <C>allow_low_confidence=True</C> to override.
            </p>

            <SubHeading>Package metadata</SubHeading>
            <CodeBlock title="metadata.py" language="python">{`# Package details
pkg = client.get_package("pdf-reader-pack")
print(f"{pkg.name} v{pkg.latest_version}")
print(f"Downloads: {pkg.download_count}")
print(f"Deprecated: {pkg.is_deprecated}")

# Install metadata (capabilities, permissions, artifact info)
meta = client.get_install_metadata("pdf-reader-pack")
print(f"Runtime: {meta.runtime}")
print(f"Entrypoint: {meta.entrypoint}")
for cap in meta.capabilities:
    print(f"  {cap.name} ({cap.capability_id})")
if meta.permissions:
    print(f"  Network: {meta.permissions.network_level}")
    print(f"  Filesystem: {meta.permissions.filesystem_level}")`}</CodeBlock>
          </section>
      </DocsShell>
    </>
  );
}
