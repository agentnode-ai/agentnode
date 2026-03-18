import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Documentation — AgentNode SDK, CLI & API Reference",
  description:
    "Complete developer documentation for AgentNode. Learn to search, install, build, and publish agent skills with the Python SDK, CLI, REST API, and ANP manifest format.",
  openGraph: {
    title: "AgentNode Documentation — SDK, CLI & API Reference",
    description:
      "Complete developer docs for building and publishing agent skills. Python SDK, CLI, REST API, and ANP format reference.",
    type: "website",
    url: "https://agentnode.net/docs",
    siteName: "AgentNode",
  },
};

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
