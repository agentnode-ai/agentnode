import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Submit your MCP server — AgentNode Catalog",
  description:
    "Submit an MCP server to the AgentNode catalog. Server-side registry re-verification, ownership check, and human review before anything is listed.",
  alternates: {
    canonical: "/mcp/submit",
  },
};

export default function McpSubmitLayout({ children }: { children: React.ReactNode }) {
  return children;
}
