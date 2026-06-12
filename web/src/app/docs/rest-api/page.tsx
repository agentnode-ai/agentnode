import type { Metadata } from "next";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  CodeBlock,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "REST API";
const DESCRIPTION = "AgentNode REST API reference: search, package metadata, resolve, policy check, publish and capability endpoints with request/response examples.";
const PATH = "/docs/rest-api";

export const metadata: Metadata = {
  title: "REST API — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "REST API — Docs | AgentNode",
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
              The AgentNode REST API provides direct HTTP access to all registry
              functionality. Base URL:{" "}
              <C>https://api.agentnode.net/v1</C>
            </p>

            <SubHeading>Authentication</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Include your API key in the <C>X-API-Key</C> header.
              Read-only endpoints (search, info) may work without
              authentication but are rate-limited.
            </p>
            <CodeBlock title="terminal" language="bash">{`curl -H "X-API-Key: ank_live_abc123def456" \\
  https://api.agentnode.net/v1/packages/pdf-reader-pack`}</CodeBlock>

            <SubHeading>Search packages</SubHeading>
            <CodeBlock title="terminal" language="bash">{`# POST /v1/search
curl -X POST "https://api.agentnode.net/v1/search" \\
  -H "Content-Type: application/json" \\
  -d '{"q": "pdf extraction", "framework": "langchain", "trust": "verified"}'

# Response:
{
  "results": [
    {
      "slug": "pdf-reader-pack",
      "name": "PDF Reader Pack",
      "version": "1.2.0",
      "summary": "Extract text, tables, and metadata from PDF documents",
      "trust_level": "trusted",
      "publisher": "agentnode-official",
      "frameworks": ["generic"],
      "capabilities": ["pdf_extraction"]
    }
  ],
  "total": 1
}`}</CodeBlock>

            <SubHeading>Get package details</SubHeading>
            <CodeBlock title="terminal" language="bash">{`# GET /v1/packages/:slug
curl "https://api.agentnode.net/v1/packages/pdf-reader-pack"

# Response:
{
  "slug": "pdf-reader-pack",
  "name": "PDF Reader Pack",
  "version": "1.2.0",
  "publisher": "agentnode-official",
  "trust_level": "trusted",
  "summary": "Extract text, tables, and metadata from PDF documents",
  "runtime": "python",
  "capabilities": [
    {
      "name": "extract_pdf",
      "capability_id": "pdf_extraction",
      "description": "Extract text, tables, and metadata from PDF documents"
    }
  ],
  "permissions": {
    "network": "none",
    "filesystem": "read",
    "code_execution": "none",
    "data_access": "input_only"
  },
  "compatibility": {
    "frameworks": ["generic"],
    "python": ">=3.10"
  }
}`}</CodeBlock>

            <SubHeading>Resolve capabilities</SubHeading>
            <CodeBlock title="terminal" language="bash">{`# POST /v1/resolve
curl -X POST "https://api.agentnode.net/v1/resolve" \\
  -H "X-API-Key: ank_live_abc123def456" \\
  -H "Content-Type: application/json" \\
  -d '{
    "capabilities": ["pdf_extraction", "web_search"],
    "framework": "langchain",
    "policy": {
      "min_trust": "verified"
    }
  }'

# Response:
{
  "results": [
    {
      "slug": "pdf-reader-pack",
      "version": "1.2.0",
      "score": 0.94,
      "trust_level": "trusted",
      "matched_capabilities": ["pdf_extraction"]
    },
    {
      "slug": "web-search-pack",
      "version": "1.0.0",
      "score": 0.92,
      "trust_level": "trusted",
      "matched_capabilities": ["web_search"]
    }
  ]
}`}</CodeBlock>

            <SubHeading>Check policy</SubHeading>
            <CodeBlock title="terminal" language="bash">{`# POST /v1/check-policy
curl -X POST "https://api.agentnode.net/v1/check-policy" \\
  -H "X-API-Key: ank_live_abc123def456" \\
  -H "Content-Type: application/json" \\
  -d '{
    "package_slug": "pdf-reader-pack",
    "policy": {
      "min_trust": "trusted",
      "max_permissions": {
        "network": "none",
        "code_execution": "none"
      }
    }
  }'

# Response:
{
  "passes": true,
  "checks": [
    { "name": "trust_level", "passed": true, "actual": "trusted", "required": "trusted" },
    { "name": "network", "passed": true, "actual": "none", "max": "none" },
    { "name": "code_execution", "passed": true, "actual": "none", "max": "none" }
  ]
}`}</CodeBlock>

            <SubHeading>Publish a package</SubHeading>
            <CodeBlock title="terminal" language="bash">{`# POST /v1/packages/publish
curl -X POST "https://api.agentnode.net/v1/packages/publish" \\
  -H "X-API-Key: ank_live_abc123def456" \\
  -H "Content-Type: multipart/form-data" \\
  -F "artifact=@./dist/my-pack-1.0.0.tar.gz" \\
  -F "manifest=@./agentnode.yaml"

# Response:
{
  "slug": "my-pack",
  "version": "1.0.0",
  "url": "https://agentnode.net/packages/my-pack",
  "trust_level": "unverified"
}`}</CodeBlock>

            <SubHeading>List capabilities</SubHeading>
            <CodeBlock title="terminal" language="bash">{`# GET /v1/capabilities
curl "https://api.agentnode.net/v1/capabilities"

# Response:
{
  "capabilities": [
    { "id": "pdf_extraction", "category": "Document Processing", "description": "Extract text and data from PDF files" },
    { "id": "web_search", "category": "Web & Browsing", "description": "Search the web and return structured results" },
    { "id": "email_sending", "category": "Communication", "description": "Compose and send emails" }
  ]
}`}</CodeBlock>

            <SubHeading>Additional endpoints</SubHeading>
            <DocTable
              headers={["Method", "Endpoint", "Description"]}
              rows={[
                ["POST", "/v1/auth/register", "Create a new account"],
                ["POST", "/v1/auth/login", "Authenticate and receive a session token"],
                ["POST", "/v1/auth/2fa/setup", "Initialize two-factor authentication"],
                ["POST", "/v1/auth/2fa/verify", "Verify a 2FA code"],
                ["GET", "/v1/packages/:slug/trust", "Get trust level details and history"],
                ["POST", "/v1/packages/:slug/reviews", "Submit a review for a package"],
                ["GET", "/v1/packages/:slug/reviews", "List reviews for a package"],
                ["POST", "/v1/packages/:slug/report", "Report a package for security or policy violations"],
                ["GET", "/v1/packages/:slug/install-info", "Get install metadata (hash, URL, dependencies)"],
                ["POST", "/v1/packages/:slug/install", "Record an installation event"],
                ["POST", "/v1/recommend", "Get pack recommendations based on installed capabilities"],
                ["POST", "/v1/packages/validate", "Validate a manifest without publishing"],
              ]}
            />
          </section>
      </DocsShell>
    </>
  );
}
