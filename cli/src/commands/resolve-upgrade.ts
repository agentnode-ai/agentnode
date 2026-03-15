/**
 * agentnode resolve-upgrade — find upgrade packages.
 * Spec §13.5
 */

import { Command } from "commander";
import chalk from "chalk";
import { resolveUpgrade } from "../api.js";

export const resolveUpgradeCommand = new Command("resolve-upgrade")
  .description("Find upgrade packages for current capabilities")
  .option("--missing <capabilities...>", "Current capability IDs to find upgrades for")
  .option("--framework <framework>", "Filter by framework")
  .option("--runtime <runtime>", "Filter by runtime")
  .option("--min-trust <level>", "Minimum trust level")
  .option("--no-shell", "Block packages requiring shell execution")
  .option("--json", "Output JSON")
  .action(async (opts) => {
    try {
      if (!opts.missing || opts.missing.length === 0) {
        throw new Error("Provide --missing <capability_id> [capability_id...]");
      }

      const policy: any = {};
      if (opts.minTrust) policy.min_trust = opts.minTrust;
      if (opts.shell === false) policy.allow_shell = false;

      const result = await resolveUpgrade(opts.missing, {
        framework: opts.framework,
        runtime: opts.runtime,
        policy,
      });

      if (opts.json) {
        console.log(JSON.stringify(result));
        return;
      }

      if (result.recommended.length === 0) {
        console.log(chalk.dim("No upgrade packages found."));
        return;
      }

      console.log(chalk.bold("\nUpgrade Recommendations\n"));

      for (const rec of result.recommended) {
        const policyColor = rec.policy_result === "allowed"
          ? chalk.green
          : rec.policy_result === "requires_approval"
          ? chalk.yellow
          : chalk.red;

        console.log(
          `  ${chalk.white(rec.package_slug)} ${chalk.dim(`v${rec.version}`)} ` +
          `— score: ${chalk.yellow(rec.compatibility_score.toFixed(2))}, ` +
          `trust: ${colorTrust(rec.trust_level)}, ` +
          `policy: ${policyColor(rec.policy_result)}`
        );

        if (rec.policy_reasons && rec.policy_reasons.length > 0) {
          for (const reason of rec.policy_reasons) {
            console.log(chalk.dim(`    → ${reason}`));
          }
        }

        console.log(chalk.cyan(`    $ ${rec.install_command}`));
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
