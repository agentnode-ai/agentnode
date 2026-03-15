import { Command } from "commander";
import { resolve } from "../api.js";

export const resolveCommand = new Command("resolve")
  .description("Resolve capabilities to ranked packages")
  .argument("<capabilities...>", "Capability IDs to resolve")
  .option("-f, --framework <name>", "Preferred framework")
  .option("-l, --limit <n>", "Max results", "5")
  .action(async (capabilities: string[], opts) => {
    try {
      const body: Record<string, any> = { limit: parseInt(opts.limit) };
      if (opts.framework) body.framework = opts.framework;

      const result = await resolve(capabilities, body);

      if (result.results.length === 0) {
        console.log("No packages match the requested capabilities.");
        return;
      }

      console.log(`Resolved ${result.total} package(s):\n`);
      for (const pkg of result.results) {
        const score = (pkg.score * 100).toFixed(1);
        console.log(`  ${pkg.slug}@${pkg.version}  score: ${score}%  [${pkg.trust_level}]`);
        console.log(`    ${pkg.summary}`);
        console.log(`    matched: ${pkg.matched_capabilities.join(", ")}\n`);
      }
    } catch (err: any) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });
