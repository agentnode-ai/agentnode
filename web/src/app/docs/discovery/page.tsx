import type { Metadata } from "next";
import {
  DocsShell,
  DocsJsonLd,
  SectionHeading,
  SubHeading,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "Search & Discovery";
const DESCRIPTION = "How AgentNode search matches capabilities, filtering by framework, trust and runtime, and how the resolution engine scores the best pack.";
const PATH = "/docs/discovery";

export const metadata: Metadata = {
  title: "Search & Discovery — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Search & Discovery — Docs | AgentNode",
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
              AgentNode search is designed for AI agent developers. Instead of
              keyword matching against package names, search queries are matched
              against capability descriptions, tool declarations, tags, and
              metadata. Results are ranked by relevance, trust level, and
              framework compatibility.
            </p>

            <SubHeading>Basic search</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode search "web scraping"

Results for "web scraping":

  webpage-extractor-pack   v1.0.0  trusted   Extract clean text and metadata from any webpage
  browser-automation-pack  v1.1.0  verified  Automate browser interactions for data extraction
  web-search-pack          v1.0.0  trusted   Search the web and retrieve structured results

3 results found`}</CodeBlock>

            <SubHeading>Filtering results</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Narrow results by framework, trust level, runtime, or capability
              ID.
            </p>
            <CodeBlock title="terminal">{`# Only show packs compatible with LangChain
$ agentnode search "pdf" --framework langchain

# Only show trusted or curated packs
$ agentnode search "email" --trust trusted

# Filter by runtime
$ agentnode search "data analysis" --runtime python

# Filter by specific capability ID
$ agentnode search --capability pdf_extraction

# Combine filters
$ agentnode search "document processing" --framework crewai --trust verified`}</CodeBlock>

            <SubHeading>Search flags</SubHeading>
            <DocTable
              headers={["Flag", "Type", "Description"]}
              rows={[
                ["--framework", "string", "Filter by framework compatibility: langchain, crewai, generic"],
                ["--trust", "string", "Minimum trust level: unverified, verified, trusted, curated"],
                ["--runtime", "string", "Filter by runtime: python"],
                ["--capability", "string", "Filter by exact capability ID from the taxonomy"],
                ["--limit", "number", "Maximum number of results to return (default: 20)"],
                ["--json", "boolean", "Output results as JSON for programmatic consumption"],
                ["--publisher", "string", "Filter by publisher namespace"],
              ]}
            />

            <SubHeading>Understanding results</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Each result shows the package slug, current version, trust level,
              and a summary. Use <C>agentnode info</C> or{" "}
              <C>agentnode explain</C> for detailed information about a specific
              pack before installing.
            </p>
            <CodeBlock title="terminal">{`$ agentnode explain pdf-reader-pack

pdf-reader-pack@1.2.0
  Publisher:    agentnode-official
  Trust:        trusted
  Runtime:      python >=3.10
  Frameworks:   langchain, crewai, generic

  Capabilities:
    - pdf_extraction: Extract text, tables, and metadata from PDF documents

  Permissions:
    Network:        none
    Filesystem:     read (reads input PDF files)
    Code Execution: none
    Data Access:    input_only

  Use Cases:
    - Extract text from PDF reports for summarization
    - Parse tables from financial PDFs
    - Read metadata and page counts from document archives

  Install: agentnode install pdf-reader-pack`}</CodeBlock>
          </section>

          <section>
            <SectionHeading id="resolution-engine">
              Resolution Engine
            </SectionHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Resolution is different from search. While search finds packages
              matching a text query, resolution takes a list of capability IDs
              your agent needs and returns the optimal packages to fill those
              gaps. The resolution engine scores candidates across multiple
              dimensions and respects policy constraints.
            </p>

            <SubHeading>How resolution scoring works</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Each candidate package receives a composite score from 0 to 1
              based on five weighted factors:
            </p>
            <DocTable
              headers={["Factor", "Weight", "Description"]}
              rows={[
                ["Capability match", "40%", "How well the pack's declared capabilities match your requested capability IDs"],
                ["Framework compatibility", "20%", "Whether the pack supports your agent's framework (LangChain, CrewAI, etc.)"],
                ["Runtime fit", "15%", "Whether the pack's runtime and version constraints match your environment"],
                ["Trust level", "15%", "Higher trust levels (curated > trusted > verified > unverified) score higher"],
                ["Permissions safety", "10%", "Packs requesting fewer permissions score higher (principle of least privilege)"],
              ]}
            />

            <SubHeading>CLI resolution</SubHeading>
            <CodeBlock title="terminal">{`$ agentnode resolve pdf_extraction web_search --framework langchain

Resolving 2 capabilities for langchain...

  pdf_extraction:
    1. pdf-reader-pack       v1.0.0  score: 0.94  trusted
    2. pdf-extractor-pack    v1.0.0  score: 0.81  verified

  web_search:
    1. web-search-pack       v1.0.0  score: 0.92  trusted
    2. browser-automation-pack v1.1.0 score: 0.73  verified

Recommended: agentnode install pdf-reader-pack web-search-pack`}</CodeBlock>

            <SubHeading>SDK resolution</SubHeading>
            <CodeBlock title="resolve.py" language="python">{`from agentnode_sdk import AgentNodeClient

client = AgentNodeClient(api_key="ank_live_abc123def456")

# Resolve multiple capability gaps at once
result = client.resolve(
    capabilities=["pdf_extraction", "web_search", "email_sending"],
    framework="langchain",
    limit=5,
)

for match in result.results:
    print(f"{match.matched_capabilities}: {match.slug} (score: {match.score})")
    print(f"  Trust: {match.trust_level}")
    print()`}</CodeBlock>

            <SubHeading>Policy constraints</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The resolution engine accepts policy constraints that
              automatically filter out non-compliant packages. This is critical
              for production deployments where agents must operate within strict
              security boundaries.
            </p>
            <CodeBlock title="terminal">{`# Only resolve packages that are trusted or curated
$ agentnode resolve pdf_extraction --trust trusted

# Only resolve packages with no network access
$ agentnode resolve pdf_extraction --policy-no-network

# Check if a specific package meets your policy
$ agentnode policy-check pdf-reader-pack --trust trusted --no-code-execution
Policy check for pdf-reader-pack@1.2.0:
  Trust level:     trusted     PASS
  Network:         none        PASS
  Filesystem:      read        PASS
  Code execution:  none        PASS
  Data access:     input_only  PASS

Package passes all policy constraints.`}</CodeBlock>
          </section>
      </DocsShell>
    </>
  );
}
