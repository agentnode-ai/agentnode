/**
 * agentnode recommend — get package recommendations.
 * Spec §13.5
 */

import { Command } from "commander";
import chalk from "chalk";
import { recommend } from "../api.js";

export const recommendCommand = new Command("recommend")
  .description("Get package recommendations for missing capabilities")
  .option("--missing <capabilities...>", "Missing capability IDs")
  .option("--framework <framework>", "Filter by framework")
  .option("--runtime <runtime>", "Filter by runtime")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    try {
      if (!opts.missing || opts.missing.length === 0) {
        throw new Error("Provide --missing <capability_id> [capability_id...]");
      }

      const result = await recommend(opts.missing, {
        framework: opts.framework,
        runtime: opts.runtime,
      });

      if (opts.json) {
        console.log(JSON.stringify(result));
        return;
      }

      console.log(chalk.bold("\nRecommendations\n"));

      for (const rec of result.recommendations) {
        console.log(chalk.cyan(`  ${rec.capability_id}:`));
        if (rec.packages.length === 0) {
          console.log(chalk.dim("    No packages found"));
        } else {
          for (const pkg of rec.packages) {
            const trust = colorTrust(pkg.trust_level);
            console.log(
              `    ${chalk.white(pkg.slug)} — ${pkg.name} ` +
              `(score: ${chalk.yellow(pkg.compatibility_score.toFixed(2))}, trust: ${trust})`
            );
          }
        }
      }
    } catch (err: any) {
      if (opts.json) {
        console.log(JSON.stringify({ error: err.message }));
      } else {
        console.error(chalk.red(`✗ ${err.message}`));
      }
      process.exit(1);
    }
  });

function colorTrust(level: string): string {
  switch (level) {
    case "curated": return chalk.blue(level);
    case "trusted": return chalk.green(level);
    case "verified": return chalk.yellow(level);
    default: return chalk.dim(level || "unknown");
  }
}
