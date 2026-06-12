import type { Metadata } from "next";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  DocTable,
  C,
} from "@/components/docs";

const TITLE = "ANP Manifest Reference";
const DESCRIPTION = "Complete reference for the ANP manifest: identity, runtime, capabilities, permissions, compatibility and tags — for single- and multi-tool packs.";
const PATH = "/docs/manifest";

export const metadata: Metadata = {
  title: "ANP Manifest Reference — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "ANP Manifest Reference — Docs | AgentNode",
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
              The <C>agentnode.yaml</C> manifest is the heart of every pack. It
              declares identity, capabilities, permissions, and compatibility
              in a single human-readable file.
            </p>

            <SubHeading>Identity fields</SubHeading>
            <DocTable
              headers={["Field", "Type", "Required", "Description"]}
              rows={[
                ["manifest_version", "string", "Yes", "ANP format version. \"0.1\" or \"0.2\". Use \"0.2\" for multi-tool packs with per-tool entrypoints."],
                ["package_id", "string", "Yes", "Unique identifier for the package. Must be lowercase, hyphenated. Example: \"pdf-reader-pack\""],
                ["package_type", "string", "Yes", "Package type. Currently \"toolpack\" is the primary type."],
                ["name", "string", "Yes", "Human-readable display name. Example: \"PDF Reader Pack\""],
                ["publisher", "string", "Yes", "Publisher namespace from your account. Example: \"agentnode-official\""],
                ["version", "string", "Yes", "Semantic version. Must follow semver: \"1.0.0\", \"2.1.3\""],
                ["summary", "string", "Yes", "One-line description (under 120 characters). Used in search results."],
                ["description", "string", "No", "Longer description with full details. Supports plain text."],
              ]}
            />

            <SubHeading>Runtime fields</SubHeading>
            <DocTable
              headers={["Field", "Type", "Required", "Description"]}
              rows={[
                ["runtime", "string", "Yes", "Execution runtime. Currently \"python\"."],
                ["entrypoint", "string", "Yes", "Package-level Python module path. Example: \"pdf_reader_pack.tool\". In v0.2, individual tools can have their own entrypoints."],
                ["install_mode", "string", "Yes", "How the pack is installed. Values: \"package\" (pip install), \"standalone\" (script)."],
                ["hosting_type", "string", "No", "Where the pack is hosted. Values: \"agentnode_hosted\" (default), \"self_hosted\", \"remote\"."],
              ]}
            />

            <SubHeading>Capabilities</SubHeading>
            <p className="mb-3 text-sm text-muted">
              The <C>capabilities</C> section declares what your pack can do.
              Each tool in the <C>tools</C> array maps to a function the pack
              exposes.
            </p>
            <DocTable
              headers={["Field", "Type", "Required", "Description"]}
              rows={[
                ["capabilities.tools", "array", "Yes", "Array of tool declarations."],
                ["tools[].name", "string", "Yes", "Tool name used in code. Example: \"extract_pdf\""],
                ["tools[].capability_id", "string", "Yes", "Standardized capability ID from the taxonomy. Example: \"pdf_extraction\""],
                ["tools[].entrypoint", "string", "v0.2", "Per-tool entrypoint in module.path:function format. Required for multi-tool v0.2 packs. Example: \"csv_analyzer_pack.tool:describe\""],
                ["tools[].description", "string", "Yes", "What this tool does, in plain language."],
                ["tools[].input_schema", "object", "No", "JSON Schema describing the tool's input parameters."],
                ["tools[].output_schema", "object", "No", "JSON Schema describing the tool's return value."],
              ]}
            />

            <SubHeading>Permissions</SubHeading>
            <p className="mb-3 text-sm text-muted">
              Every pack must explicitly declare what system resources it
              accesses. This is not optional -- it is enforced at publish time
              and surfaced to users before installation.
            </p>
            <DocTable
              headers={["Field", "Values", "Description"]}
              rows={[
                ["permissions.network.level", "none | restricted | unrestricted", "Network access. \"none\" = no outbound calls. \"restricted\" = specific domains only. \"unrestricted\" = any network access."],
                ["permissions.network.justification", "string", "Why this permission level is needed (recommended for restricted/unrestricted)."],
                ["permissions.filesystem.level", "none | temp | read | write", "File system access. \"none\" = no FS access. \"temp\" = temp directory only. \"read\" = read files. \"write\" = read and write."],
                ["permissions.code_execution.level", "none | sandboxed | full", "Code execution. \"none\" = no code exec. \"sandboxed\" = restricted sandbox. \"full\" = unrestricted execution."],
                ["permissions.data_access.level", "input_only | output_only | bidirectional", "Data flow direction. \"input_only\" = reads input, does not send data out. \"bidirectional\" = sends and receives."],
              ]}
            />

            <SubHeading>Compatibility</SubHeading>
            <DocTable
              headers={["Field", "Type", "Required", "Description"]}
              rows={[
                ["compatibility.frameworks", "array", "No", "Auto-defaults to [\"generic\"]. ANP packages work across all frameworks automatically."],
                ["compatibility.python", "string", "No", "Python version constraint. Example: \">=3.10\""],
              ]}
            />

            <SubHeading>Tags</SubHeading>
            <p className="mb-3 text-sm text-muted">
              An array of lowercase string tags for search and categorization.
              Example: <C>{`["pdf", "extraction", "documents"]`}</C>. Tags
              are free-form but should describe the domain and use case.
            </p>
          </section>
      </DocsShell>
    </>
  );
}
