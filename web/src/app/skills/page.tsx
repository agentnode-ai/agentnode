import type { Metadata } from "next";
import Link from "next/link";
import PackageSearch from "@/components/PackageSearch";
import { LibraryHero } from "@/components/PackageLibrary";

export const metadata: Metadata = {
  title: "Skills — Prompt-Only Capabilities for AI Agents",
  description:
    "Browse verified skills: prompt-only instruction packages (SKILL.md) an AI agent loads to gain a capability — no code, safe by construction.",
  alternates: { canonical: "/skills" },
};

export const revalidate = 300;

export default function SkillsPage() {
  return (
    <div className="flex flex-col">
      <LibraryHero
        eyebrow="Skills"
        title={
          <>
            Prompt-only <span className="text-primary">skills</span> your agent loads
          </>
        }
        subtitle="Instruction packages (SKILL.md) that teach your agent a capability — writing release notes, reviewing code, structuring plans. No code runs, so no sandbox is needed."
      />

      <section className="border-b border-border">
        <PackageSearch
          fixed={{ package_type: "skill" }}
          basePath="/skills"
          heading={null}
          autoFocus={false}
          showCapability={false}
        />
      </section>

      {/* Explainer below the library */}
      <section>
        <div className="mx-auto max-w-4xl px-4 sm:px-6 py-14">
          <h2 className="mb-6 text-center text-2xl font-bold text-foreground">
            What is a skill?
          </h2>
          <div className="grid gap-6 sm:grid-cols-3">
            <div className="rounded-xl border border-border bg-card p-6">
              <h3 className="mb-2 font-semibold text-foreground">Instructions, not code</h3>
              <p className="text-sm leading-relaxed text-muted">
                A skill is a SKILL.md document plus manifest. Your agent loads
                the instructions with{" "}
                <code className="rounded bg-background px-1.5 py-0.5 font-mono text-xs">load_skill()</code>{" "}
                and applies them — nothing executes.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6">
              <h3 className="mb-2 font-semibold text-foreground">Safe by construction</h3>
              <p className="text-sm leading-relaxed text-muted">
                The validator enforces that skills carry no entrypoint, no
                network, no filesystem, no code execution. That&apos;s why every
                skill shows &ldquo;No sandbox needed&rdquo;.
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-6">
              <h3 className="mb-2 font-semibold text-foreground">Easy to create</h3>
              <p className="text-sm leading-relaxed text-muted">
                Describe it in plain language and the{" "}
                <Link href="/builder" className="text-primary hover:underline">AI Builder</Link>{" "}
                writes the SKILL.md — or import a Claude-style SKILL.md via{" "}
                <Link href="/import" className="text-primary hover:underline">Import</Link>.
              </p>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
