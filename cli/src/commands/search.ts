import { Command } from "commander";
import { search } from "../api.js";

export const searchCommand = new Command("search")
  .description("Search for packages in the AgentNode registry")
  .argument("<query>", "Search query")
  .option("-t, --type <type>", "Filter by package type (agent, toolpack, upgrade)")
  .option("-c, --capability <id>", "Filter by capability ID")
  .option("-f, --framework <name>", "Filter by framework")
  .option("-l, --limit <n>", "Max results", "10")
  .action(async (query: string, opts) => {
    try {
      // P1-C7: Validate --limit. Previously a negative or non-numeric
      // value was forwarded verbatim to the server. Clamp to [1, 100].
      const parsedLimit = Number(opts.limit);
      if (!Number.isFinite(parsedLimit) || parsedLimit < 1) {
        throw new Error(
          `--limit must be a positive integer (got '${opts.limit}')`,
        );
      }
      const limit = Math.min(100, Math.floor(parsedLimit));
      const params: Record<string, any> = { per_page: limit };
      if (opts.type) params.package_type = opts.type;
      if (opts.capability) params.capability_id = opts.capability;
      if (opts.framework) params.framework = opts.framework;

      const result = await search(query, params);

      if (result.hits.length === 0) {
        console.log("No packages found.");
        return;
      }

      // P1-C8: Respect terminal width when printing long summaries so
      // narrow terminals don't wrap into unreadable blocks. Fall back to
      // 80 columns if stdout is not a TTY (e.g. piped to a file).
      const termWidth = process.stdout.columns && process.stdout.columns > 20
        ? process.stdout.columns
        : 80;
      const summaryBudget = Math.max(20, termWidth - 4 /* "    " indent */);
      const truncate = (s: string, n: number) =>
        s.length > n ? s.slice(0, n - 1) + "…" : s;

      console.log(`Found ${result.total} package(s):\n`);
      for (const hit of result.hits) {
        const trust = hit.trust_level !== "unverified" ? ` [${hit.trust_level}]` : "";
        console.log(`  ${hit.slug}@${hit.latest_version || "?"}${trust}`);
        console.log(`    ${truncate(hit.summary || "", summaryBudget)}`);
        console.log(`    downloads: ${hit.download_count}  type: ${hit.package_type}\n`);
      }
    } catch (err: any) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });
