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
      const params: Record<string, any> = { per_page: Number(opts.limit) };
      if (opts.type) params.package_type = opts.type;
      if (opts.capability) params.capability_id = opts.capability;
      if (opts.framework) params.framework = opts.framework;

      const result = await search(query, params);

      if (result.hits.length === 0) {
        console.log("No packages found.");
        return;
      }

      console.log(`Found ${result.total} package(s):\n`);
      for (const hit of result.hits) {
        const trust = hit.trust_level !== "unverified" ? ` [${hit.trust_level}]` : "";
        console.log(`  ${hit.slug}@${hit.latest_version || "?"}${trust}`);
        console.log(`    ${hit.summary}`);
        console.log(`    downloads: ${hit.download_count}  type: ${hit.package_type}\n`);
      }
    } catch (err: any) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });
