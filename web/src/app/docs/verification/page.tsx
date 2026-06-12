import type { Metadata } from "next";
import {
  DocsShell,
  DocsJsonLd,
  SubHeading,
  C,
} from "@/components/docs";

const TITLE = "Package Verification";
const DESCRIPTION = "How AgentNode verifies packages: install, import, smoke and test stages, multi-run quality checks, scoring, and verification tiers.";
const PATH = "/docs/verification";

export const metadata: Metadata = {
  title: "Package Verification — Docs | AgentNode",
  description: DESCRIPTION,
  alternates: { canonical: PATH },
  openGraph: {
    title: "Package Verification — Docs | AgentNode",
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
              AgentNode verifies every package on publish in a real sandbox
              and computes a verification score from 0&ndash;100. The score is
              based on evidence &mdash; not self-reported badges. Scores are
              visible on every package page and factor into search ranking.
            </p>

            <SubHeading>Verification steps</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Each package goes through four checks, each contributing points
              to the overall score:
            </p>
            <div className="mb-6 space-y-3">
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="font-mono text-sm font-bold text-green-400">
                    1. Install
                  </p>
                  <span className="text-xs text-muted font-mono">15 pts</span>
                </div>
                <p className="text-sm text-muted">
                  The package is installed in a clean virtual environment. If
                  installation fails (missing dependencies, build errors), the
                  package is automatically quarantined.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="font-mono text-sm font-bold text-green-400">
                    2. Import
                  </p>
                  <span className="text-xs text-muted font-mono">15 pts</span>
                </div>
                <p className="text-sm text-muted">
                  All declared tool entrypoints are imported and checked for
                  existence and callability. If any entrypoint is missing or
                  not callable, the package is quarantined.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="font-mono text-sm font-bold text-yellow-400">
                    3. Smoke test
                  </p>
                  <span className="text-xs text-muted font-mono">25 pts</span>
                </div>
                <p className="text-sm text-muted">
                  Tools are called with test inputs generated from their JSON
                  schema. Results are classified as{" "}
                  <span className="text-green-400">passed</span> (25 pts),{" "}
                  <span className="text-yellow-400">inconclusive</span> (0&ndash;12
                  pts depending on reason), or{" "}
                  <span className="text-red-400">failed</span> (0 pts). Smoke
                  failures do not block the package but reduce the score.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between mb-1">
                  <p className="font-mono text-sm font-bold text-yellow-400">
                    4. Tests
                  </p>
                  <span className="text-xs text-muted font-mono">15 pts</span>
                </div>
                <p className="text-sm text-muted">
                  If the package includes a test suite, it is executed with
                  pytest. Real tests that pass earn 15 pts. Auto-generated tests
                  earn 8 pts. No tests: 3 pts. Integration tests marked with{" "}
                  <C>@pytest.mark.integration</C> are skipped.
                </p>
              </div>
            </div>

            <SubHeading>Quality checks (multi-run)</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              After a successful smoke test, the same input is run multiple
              times to measure stability:
            </p>
            <div className="mb-6 overflow-hidden rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-card">
                    <th className="px-4 py-2 text-left font-medium text-foreground">Check</th>
                    <th className="px-4 py-2 text-left font-medium text-foreground">Points</th>
                    <th className="px-4 py-2 text-left font-medium text-foreground">What it measures</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  <tr><td className="px-4 py-2 font-mono text-foreground">Reliability</td><td className="px-4 py-2 text-muted">0&ndash;10</td><td className="px-4 py-2 text-muted">Proportion of runs that succeed (e.g. 3/3 = 10 pts)</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-foreground">Determinism</td><td className="px-4 py-2 text-muted">0&ndash;5</td><td className="px-4 py-2 text-muted">Output consistency across runs (same hash = 5 pts)</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-foreground">Contract</td><td className="px-4 py-2 text-muted">0&ndash;10</td><td className="px-4 py-2 text-muted">Return value is serializable, non-null, structurally valid</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-foreground">Warnings</td><td className="px-4 py-2 text-muted">&minus;2 each</td><td className="px-4 py-2 text-muted">Deprecation warnings, unsafe patterns (max &minus;10)</td></tr>
                </tbody>
              </table>
            </div>

            <SubHeading>Verification tiers</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              The total score maps to a tier displayed on the package page and
              in search results:
            </p>
            <div className="mb-6 grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="rounded-lg border border-yellow-500/20 bg-yellow-500/5 p-4 text-center">
                <p className="text-sm font-bold text-yellow-400">Gold</p>
                <p className="text-xs text-muted">90&ndash;100</p>
              </div>
              <div className="rounded-lg border border-green-500/20 bg-green-500/5 p-4 text-center">
                <p className="text-sm font-bold text-green-400">Verified</p>
                <p className="text-xs text-muted">70&ndash;89</p>
              </div>
              <div className="rounded-lg border border-yellow-500/20 bg-yellow-500/5 p-4 text-center">
                <p className="text-sm font-bold text-yellow-400">Partial</p>
                <p className="text-xs text-muted">50&ndash;69</p>
              </div>
              <div className="rounded-lg border border-zinc-500/20 bg-zinc-500/5 p-4 text-center">
                <p className="text-sm font-bold text-zinc-500">Unverified</p>
                <p className="text-xs text-muted">&lt;50</p>
              </div>
            </div>

            <SubHeading>Inconclusive reasons &amp; partial credit</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Not every tool can be fully tested in a sandbox. A tool that
              needs an API key is not broken &mdash; it just cannot be smoke-tested
              without credentials. We classify <em>why</em> a smoke test is
              inconclusive and score accordingly:
            </p>
            <div className="mb-6 overflow-hidden rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-card">
                    <th className="px-4 py-2 text-left font-medium text-foreground">Reason</th>
                    <th className="px-4 py-2 text-left font-medium text-foreground">Smoke pts</th>
                    <th className="px-4 py-2 text-left font-medium text-foreground">Meaning</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  <tr><td className="px-4 py-2 font-mono text-yellow-400">needs_credentials</td><td className="px-4 py-2 text-muted">12/25</td><td className="px-4 py-2 text-muted">Requires API keys not available in sandbox</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-yellow-400">missing_system_dependency</td><td className="px-4 py-2 text-muted">12/25</td><td className="px-4 py-2 text-muted">Requires Chromium, FFmpeg, etc.</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-yellow-400">needs_binary_input</td><td className="px-4 py-2 text-muted">12/25</td><td className="px-4 py-2 text-muted">Requires real PDF, image, or audio files</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-yellow-400">external_network_blocked</td><td className="px-4 py-2 text-muted">12/25</td><td className="px-4 py-2 text-muted">Needs network access blocked in sandbox</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-zinc-400">not_implemented</td><td className="px-4 py-2 text-muted">0/25</td><td className="px-4 py-2 text-muted">Stub package, raises NotImplementedError</td></tr>
                  <tr><td className="px-4 py-2 font-mono text-zinc-400">unknown_smoke_condition</td><td className="px-4 py-2 text-muted">8/25</td><td className="px-4 py-2 text-muted">Ambiguous error &mdash; may be broken or just missing data</td></tr>
                </tbody>
              </table>
            </div>

            <SubHeading>How to improve your score</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              As a publisher, there are several things you can do to maximize
              your verification score:
            </p>
            <div className="mb-6 space-y-3">
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-1 text-sm font-semibold text-foreground">
                  Include real tests
                </p>
                <p className="text-sm text-muted">
                  A passing test suite earns 15 pts (vs. 3 pts with no tests).
                  Use <C>pytest</C> and place tests in a{" "}
                  <C>tests/</C> directory. Mark integration tests that need
                  external services with <C>@pytest.mark.integration</C>.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-1 text-sm font-semibold text-foreground">
                  Define a complete input schema
                </p>
                <p className="text-sm text-muted">
                  Declare <C>input_schema</C> with <C>properties</C>,{" "}
                  <C>required</C>, and <C>type</C> for every field. Use{" "}
                  <C>enum</C> for constrained fields and <C>default</C> values
                  where appropriate. The more schema detail, the better our
                  test input generation.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-1 text-sm font-semibold text-foreground">
                  Add schema examples
                </p>
                <p className="text-sm text-muted">
                  Include an <C>examples</C> key in your input schema with a
                  working example input. This is the highest-confidence test
                  input and is tried first.
                </p>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-1 text-sm font-semibold text-foreground">
                  Return serializable values
                </p>
                <p className="text-sm text-muted">
                  Tools should return JSON-serializable values (dicts, lists,
                  strings). Returning <C>None</C> or non-serializable objects
                  reduces the contract score.
                </p>
              </div>
            </div>

            <SubHeading>Quarantine behavior</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Packages are automatically quarantined if installation or import
              fails. Quarantined packages are hidden from search results and
              cannot be installed. Other issues (smoke test, unit tests) reduce
              the score but do not block usage.
            </p>

            <SubHeading>Continuous re-verification</SubHeading>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Scores are not static. Every package is automatically
              re-verified on every publish. If a dependency update breaks
              something, the score drops and users see it before their agent
              does. Admins can also trigger targeted re-verification at any
              time.
            </p>

            <SubHeading>What verification guarantees</SubHeading>
            <div className="mb-6 grid gap-4 sm:grid-cols-2">
              <div className="rounded-lg border border-green-500/20 bg-green-500/5 p-4">
                <p className="mb-2 text-sm font-semibold text-green-400">Guaranteed</p>
                <ul className="space-y-1 text-sm text-muted">
                  <li>Can be installed in a clean environment</li>
                  <li>All declared entrypoints exist and are callable</li>
                  <li>Package structure is valid</li>
                  <li>Score reflects real sandbox execution</li>
                </ul>
              </div>
              <div className="rounded-lg border border-border bg-card p-4">
                <p className="mb-2 text-sm font-semibold text-muted">Not guaranteed</p>
                <ul className="space-y-1 text-sm text-muted">
                  <li>Correct behavior with real-world data</li>
                  <li>Availability of external services</li>
                  <li>Full test coverage</li>
                  <li>Tools requiring credentials/system deps work correctly</li>
                </ul>
              </div>
            </div>
            <p className="mb-4 text-sm leading-relaxed text-muted">
              Verification is evidence-based, not absolute. It filters out
              broken packages and scores what it can prove. &ldquo;Partially
              Verified&rdquo; means &ldquo;not fully testable in a
              sandbox&rdquo; &mdash; not &ldquo;broken.&rdquo;
            </p>
          </section>
      </DocsShell>
    </>
  );
}
